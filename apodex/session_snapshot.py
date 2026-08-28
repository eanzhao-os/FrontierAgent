"""Atomic semantic snapshot of a TerminalSession for Web first-load and reconnect."""

from __future__ import annotations

from typing import Any

_TRANSCRIPT_LIMIT = 300


def _message_content(message: object) -> tuple[str, str]:
    if isinstance(message, dict):
        content = message.get("content", "")
        role = str(message.get("role") or "")
    else:
        content = getattr(message, "content", "")
        role = str(getattr(message, "role", "") or "")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(str(item))
        return role, "\n".join(parts)
    return role, str(content or "")


def transcript_page(
    session: Any,
    *,
    before: str | None = None,
    limit: int = _TRANSCRIPT_LIMIT,
) -> dict[str, Any]:
    messages = list(getattr(session, "display_history", None) or getattr(session, "history", None) or [])
    all_blocks: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        role, content = _message_content(message)
        all_blocks.append({"id": f"b{index}", "role": role, "content": content})
    end = len(all_blocks)
    if before:
        for index, block in enumerate(all_blocks):
            if block["id"] == before:
                end = index
                break
    start = max(0, end - limit)
    blocks = all_blocks[start:end]
    has_older = start > 0
    before_id = blocks[0]["id"] if has_older and blocks else None
    return {"blocks": blocks, "has_older": has_older, "before": before_id}


def _transcript_blocks(session: Any) -> tuple[list[dict[str, Any]], bool, str | None]:
    page = transcript_page(session)
    return page["blocks"], page["has_older"], page["before"]


def _config_dict(session: Any) -> dict[str, Any]:
    status = session.runtime_config_status()
    return {
        "ok": bool(status.ok),
        "mode": status.mode,
        "provider": status.provider,
        "model": status.model,
        "endpoint_host": status.endpoint_host,
        "api_key_env": status.api_key_env,
        "api_key_configured": status.api_key_configured,
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "env_var": issue.env_var,
                "blocking": issue.blocking,
            }
            for issue in status.issues
        ],
    }


def _usage_dict(session: Any) -> dict[str, Any]:
    usage = getattr(session, "usage", None)
    if usage is None:
        return {}
    to_dict = getattr(usage, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    return {
        "input": getattr(usage, "input", 0),
        "output": getattr(usage, "output", 0),
        "total": getattr(usage, "total", 0),
    }


def _attachments(session: Any) -> list[dict[str, Any]]:
    manager = getattr(session, "attachments", None)
    if manager is None:
        return []
    items = []
    for item in manager.list():
        items.append({
            "relative_path": item.relative_path,
            "agent_path": item.agent_path,
            "size": item.size,
        })
    return items


def _changes(session: Any) -> dict[str, Any]:
    journal = getattr(session, "journal", None)
    if journal is None:
        return {"stats": [], "diff": "", "observed_only": []}
    stats, diff = journal.report()
    return {
        "stats": stats,
        "diff": diff,
        "observed_only": list(journal.observed_only()),
    }


def _renderer_mirror(session: Any) -> tuple[dict[str, Any], dict[str, str]]:
    renderer = getattr(session, "r", None)
    phase = str(getattr(renderer, "phase", "idle") or "idle")
    presentation = {
        "phase": phase,
        "elapsed_seconds": getattr(renderer, "elapsed_seconds", None),
        "tool_count": int(getattr(renderer, "tool_count", 0) or 0),
        "queued": int(getattr(renderer, "queued_count", 0) or 0),
    }
    stream = {
        "thinking": str(getattr(renderer, "_current_thinking", "") or ""),
        "content": str(getattr(renderer, "_current_content", "") or ""),
    }
    return presentation, stream


def build_session_snapshot(
    session: Any,
    *,
    revision: int,
    sequence: int,
    runtime_status: str,
    pending_approval: dict[str, Any] | None,
) -> dict[str, Any]:
    blocks, has_older, before = _transcript_blocks(session)
    presentation, stream = _renderer_mirror(session)
    return {
        "revision": revision,
        "sequence": sequence,
        "session": {
            "id": session.session_id,
            "name": getattr(session, "session_name", "") or "",
            "mode": session.mode,
            "model": getattr(session.cfg, "model", ""),
            "cwd": session.cwd,
        },
        "runtime": {
            "status": runtime_status,
            "config": _config_dict(session),
            "usage": _usage_dict(session),
        },
        "presentation": presentation,
        "stream": stream,
        "transcript": {"blocks": blocks, "has_older": has_older, "before": before},
        "plan": {"items": [], "summary": ""},
        "activity": {"records": [], "subagents": [], "totals": {}},
        "attachments": _attachments(session),
        "artifacts": [],
        "changes": _changes(session),
        "pending_approval": pending_approval,
    }
