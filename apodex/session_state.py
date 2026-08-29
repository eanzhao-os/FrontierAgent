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
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home).resolve()
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
            # A session becomes real only on first real input (or an explicit
            # user action on it) — empty shells stay out of the roster.
            if not (
                state.get("history")
                or state.get("display_history")
                or state.get("name")
                or state.get("archived")
                or state.get("pinned")
            ):
                continue
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
                "archived": bool(state.get("archived", False)),
                "pinned": bool(state.get("pinned", False)),
            })
        except Exception:
            continue
    return sessions


def _set_session_flag(
    session_id: str,
    key: str,
    value: bool,
    extra_roots: list[str] | None = None,
) -> bool:
    """Update a flag field in session.json for the given session id."""
    return _set_session_field(session_id, key, bool(value), extra_roots)


def _set_session_field(
    session_id: str,
    key: str,
    value,
    extra_roots: list[str] | None = None,
) -> bool:
    """Update a field in session.json for the given session id."""
    import json

    from apodex.run_layout import runs_root

    all_roots = discover_all_run_roots(extra_roots)
    all_roots.extend(_legacy_session_roots())
    if extra_roots:
        for r in extra_roots:
            all_roots.append(runs_root(r).resolve())

    updated = False
    for root in all_roots:
        for cand in (root / session_id / "session.json", root / f"{session_id}.json"):
            if cand.is_file():
                try:
                    data = json.loads(cand.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        data[key] = value
                        cand.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
                        updated = True
                except Exception:
                    pass
    return updated


def set_session_archived(
    session_id: str,
    *,
    archived: bool = True,
    extra_roots: list[str] | None = None,
) -> bool:
    """Update archived flag in session.json for the given session id."""
    return _set_session_flag(session_id, "archived", archived, extra_roots)


def set_session_pinned(
    session_id: str,
    *,
    pinned: bool = True,
    extra_roots: list[str] | None = None,
) -> bool:
    """Update pinned flag in session.json for the given session id."""
    return _set_session_flag(session_id, "pinned", pinned, extra_roots)


def set_session_name(
    session_id: str,
    name: str,
    extra_roots: list[str] | None = None,
) -> bool:
    """Update the display name in session.json for the given session id."""
    return _set_session_field(session_id, "name", name, extra_roots)


def delete_session_run(
    session_id: str,
    extra_roots: list[str] | None = None,
) -> bool:
    """Permanently delete session run directory and legacy session files."""
    import shutil

    from apodex.run_layout import runs_root

    deleted = False

    state = load_session_state(session_id)
    if state and isinstance(state, dict) and "_run_dir" in state:
        rd = Path(state["_run_dir"])
        if rd.is_dir():
            try:
                shutil.rmtree(rd)
                deleted = True
            except Exception:
                pass

    all_roots = discover_all_run_roots(extra_roots)
    all_roots.extend(_legacy_session_roots())
    if extra_roots:
        for r in extra_roots:
            all_roots.append(runs_root(r).resolve())

    for root in all_roots:
        cand = root / session_id
        if cand.is_dir():
            try:
                shutil.rmtree(cand)
                deleted = True
            except Exception:
                pass
        cand_legacy = root / f"{session_id}.json"
        if cand_legacy.is_file():
            try:
                cand_legacy.unlink()
                deleted = True
            except Exception:
                pass
    return deleted

