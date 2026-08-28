import os
import uuid
from pathlib import Path
from typing import Any


def new_session_id(mode: str) -> str:
    """Return a readable local-time run id with an explicit UTC offset."""
    from apodex.run_layout import new_run_timestamp

    timestamp, _utc, _zone = new_run_timestamp()
    return f"{timestamp}-{mode}-{uuid.uuid4().hex[:4]}"


def _session_state_path(session_id: str) -> str:
    from apodex.run_layout import run_dir

    return str(run_dir(session_id, create=False) / "session.json")


def _real_user_home() -> Path:
    import pwd
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        return Path(os.path.expanduser("~")).resolve()


def _legacy_session_roots() -> list[Path]:
    roots = [Path(_real_user_home() / ".apodex" / "sessions")]
    configured = os.environ.get("APODEX_LEGACY_SESSION_ROOTS", "")
    roots.extend(Path(value) for value in configured.split(os.pathsep) if value)
    return roots


def discover_all_run_roots(extra_roots: list[str] | None = None) -> list[Path]:
    """Return all configured .apodex/runs directories from configurable workspaces."""
    from apodex.workspace_config import get_all_configured_run_roots
    return get_all_configured_run_roots(extra_roots)


def load_session_state(session_id: str) -> dict | None:
    """Load a persisted session checkpoint by id (for ``--resume``), or None."""
    import json
    try:
        candidates = [_session_state_path(session_id)]
        candidates.extend(str(root / f"{session_id}.json") for root in _legacy_session_roots())
        for root in discover_all_run_roots():
            candidates.append(str(root / session_id / "session.json"))
            candidates.append(str(root / f"{session_id}.json"))

        for candidate in candidates:
            try:
                with open(candidate, encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        cand_path = Path(candidate)
                        if cand_path.name == "session.json":
                            data["_run_dir"] = str(cand_path.parent.resolve())
                        return data
            except OSError:
                continue
    except Exception:
        return None


def list_saved_sessions(
    extra_roots: list[str] | None = None,
    workspace: str | Path | None = None,
) -> list[dict[str, Any]]:
    """Return saved-session metadata, newest first, for ``--resume`` listings.

    Searches across workspace-local run roots as well as machine-wide discovered roots.
    """
    import json
    from apodex.run_layout import local_time_from_timestamp, runs_root

    all_roots = discover_all_run_roots(extra_roots)
    if workspace:
        all_roots.append(runs_root(workspace).resolve())

    paths: set[Path] = set()
    for root in all_roots:
        if root.is_dir():
            try:
                for p in root.glob("*/session.json"):
                    paths.add(p.resolve())
                for p in root.glob("*.json"):
                    if p.name != "session.json":
                        paths.add(p.resolve())
            except Exception:
                pass

    sorted_paths = sorted(
        paths,
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )

    sessions: list[dict[str, Any]] = []
    seen: set[str] = set()
    for path in sorted_paths:
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(state, dict):
                continue
            fallback = path.parent.name if path.name == "session.json" else path.stem
            session_id = str(state.get("session_id") or fallback)
            if session_id in seen:
                continue
            seen.add(session_id)
            run_dir_path = str(path.parent.resolve()) if path.name == "session.json" else ""
            default_cwd = str(path.parent.parent.parent) if path.name == "session.json" else "unknown directory"
            sessions.append({
                "session_id": session_id,
                "name": str(state.get("name") or ""),
                "mode": str(state.get("mode") or "unknown"),
                "cwd": str(state.get("cwd") or default_cwd),
                "message_count": len(state.get("history") or state.get("display_history") or []),
                "modified_at": local_time_from_timestamp(path.stat().st_mtime),
                "run_dir": run_dir_path,
            })
        except Exception:
            continue
    return sessions
