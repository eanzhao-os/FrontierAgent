import contextlib
import json
import os
import pwd
from pathlib import Path


def get_real_user_home() -> Path:
    """Resolve the real user home directory, respecting HOME if set."""
    env_home = os.environ.get("HOME")
    if env_home:
        return Path(env_home).resolve()
    try:
        return Path(pwd.getpwuid(os.getuid()).pw_dir).resolve()
    except Exception:
        return Path(os.path.expanduser("~")).resolve()


def get_config_file_path() -> Path:
    """Path to ~/.apodex/workspaces.json"""
    home = get_real_user_home()
    config_dir = home / ".apodex"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / "workspaces.json"


def load_configured_paths() -> list[str]:
    """Load configured workspace directories from config file and environment variables."""
    paths: list[str] = []
    seen: set[str] = set()

    def _add(raw_p: str) -> None:
        if not raw_p or not isinstance(raw_p, str):
            return
        p_str = os.path.expanduser(raw_p.strip())
        if p_str:
            resolved = str(Path(p_str).resolve())
            if resolved not in seen:
                seen.add(resolved)
                paths.append(resolved)

    # 1. Environment variables
    for env_var in ("FRONTIER_AGENT_WORKSPACES", "APODEX_WORKSPACES", "APODEX_RUN_ROOTS"):
        val = os.environ.get(env_var, "")
        if val:
            for item in val.replace(";", ":").replace(",", ":").split(":"):
                _add(item)

    # 2. Config file ~/.apodex/workspaces.json
    cfg_file = get_config_file_path()
    if cfg_file.exists():
        try:
            data = json.loads(cfg_file.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for p in data.get("workspaces", []):
                    _add(p)
            elif isinstance(data, list):
                for p in data:
                    _add(p)
        except Exception:
            pass

    # 3. Always include current working directory if not present
    _add(os.getcwd())

    return paths


def save_configured_paths(paths: list[str]) -> None:
    """Save the list of configured workspace directories to ~/.apodex/workspaces.json"""
    cfg_file = get_config_file_path()
    clean_paths = []
    seen = set()
    for p in paths:
        if p and isinstance(p, str):
            resolved = str(Path(os.path.expanduser(p.strip())).resolve())
            if resolved not in seen:
                seen.add(resolved)
                clean_paths.append(resolved)
    with contextlib.suppress(Exception):
        cfg_file.write_text(json.dumps({"workspaces": clean_paths}, indent=2, ensure_ascii=False), encoding="utf-8")


def add_workspace_path(path: str) -> list[str]:
    """Add a new workspace directory to configuration."""
    current = load_configured_paths()
    resolved = str(Path(os.path.expanduser(path.strip())).resolve())
    if resolved not in current:
        current.append(resolved)
        save_configured_paths(current)
    return current


def remove_workspace_path(path: str) -> list[str]:
    """Remove a workspace directory from configuration."""
    current = load_configured_paths()
    resolved = str(Path(os.path.expanduser(path.strip())).resolve())
    updated = [p for p in current if p != resolved]
    save_configured_paths(updated)
    return updated


def get_all_configured_run_roots(extra_roots: list[str] | None = None) -> list[Path]:
    """Return all .apodex/runs paths from all configured workspaces without hardcoding."""
    roots: set[Path] = set()

    # 1. Global home roots
    home = get_real_user_home()
    if (home / ".apodex" / "runs").is_dir():
        roots.add((home / ".apodex" / "runs").resolve())
    if (home / ".apodex" / "sessions").is_dir():
        roots.add((home / ".apodex" / "sessions").resolve())

    # 2. Configured workspace paths
    for ws in load_configured_paths():
        ws_path = Path(ws).resolve()
        # Direct .apodex/runs in workspace
        runs_dir = ws_path / ".apodex" / "runs"
        if runs_dir.is_dir():
            roots.add(runs_dir.resolve())
        elif ws_path.name == "runs" and ws_path.is_dir():
            roots.add(ws_path)
        elif ws_path.is_dir():
            # If workspace itself exists, also check if .apodex exists
            roots.add((ws_path / ".apodex" / "runs").resolve())

    # 3. Any explicit extra roots
    for extra in (extra_roots or []):
        if extra:
            p = Path(os.path.expanduser(extra)).resolve()
            if p.is_dir():
                roots.add(p)

    return sorted(roots)
