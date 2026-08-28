"""Allowed-root checks for Web file, preview, and raw routes."""

from __future__ import annotations

from pathlib import Path


def allowed_file_path(
    path: str,
    *,
    cwd: str,
    session_id: str,
    run_roots: list | None,
    inputs_dir: str | None,
    outputs_dir: str | None,
) -> Path | None:
    del session_id
    try:
        resolved = Path(path).expanduser().resolve()
    except OSError:
        return None
    roots: list[Path] = []
    for raw in (cwd, *(run_roots or []), inputs_dir, outputs_dir):
        if not raw:
            continue
        try:
            roots.append(Path(raw).expanduser().resolve())
        except OSError:
            continue
    for root in roots:
        try:
            resolved.relative_to(root)
        except ValueError:
            continue
        return resolved
    return None
