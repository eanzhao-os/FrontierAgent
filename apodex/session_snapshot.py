"""Atomic semantic snapshot of a TerminalSession for Web first-load and reconnect."""

from __future__ import annotations

import json
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


def _tool_call_names(session: Any) -> dict[str, str]:
    """Map tool_call_id → tool name from workflow turns, when persisted.

    ``role: "tool"`` wire messages carry only the call id; the name lives on
    the assistant ``tool_calls`` entries of the owning turn.
    """
    names: dict[str, str] = {}
    for turn in getattr(session, "workflow_turns", None) or []:
        if not isinstance(turn, dict):
            continue
        for message in turn.get("messages", []) or []:
            if not isinstance(message, dict):
                continue
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                call_id = str(call.get("id") or "")
                function = call.get("function") or {}
                name = str(function.get("name") or "") if isinstance(function, dict) else ""
                if call_id and name:
                    names[call_id] = name
    return names


def _tool_call_args(messages: list[Any]) -> dict[str, dict[str, Any]]:
    """Map tool_call_id → parsed arguments from assistant ``tool_calls``.

    Wire messages carry ``function.arguments`` as a JSON string; chips need
    the parsed dict to show a meaningful summary after a reload.
    """
    args_by_id: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, dict):
            continue
        for call in message.get("tool_calls") or []:
            if not isinstance(call, dict):
                continue
            call_id = str(call.get("id") or "")
            function = call.get("function") or {}
            raw = function.get("arguments") if isinstance(function, dict) else None
            if not call_id:
                continue
            if isinstance(raw, dict):
                args_by_id[call_id] = raw
            elif isinstance(raw, str) and raw.strip():
                try:
                    parsed = json.loads(raw)
                except ValueError:
                    continue
                if isinstance(parsed, dict):
                    args_by_id[call_id] = parsed
    return args_by_id


def transcript_page(
    session: Any,
    *,
    before: str | None = None,
    limit: int = _TRANSCRIPT_LIMIT,
) -> dict[str, Any]:
    """Structured transcript blocks so a reload keeps the rich card display.

    ``kind`` is one of ``user`` / ``thinking`` / ``tool`` / ``text``. Tool
    blocks carry ``call_id``, ``name``, parsed ``args``, ``duration_ms`` and
    ``is_error`` — everything ToolChipRow renders live — so a reloaded
    session looks identical to a live one. Empty assistant separators are
    dropped; the legacy ``role`` key stays for older clients.
    """
    messages = list(getattr(session, "display_history", None) or getattr(session, "history", None) or [])
    tool_names = _tool_call_names(session)
    tool_args = _tool_call_args(messages)
    all_blocks: list[dict[str, Any]] = []
    for index, message in enumerate(messages):
        if isinstance(message, dict):
            role = str(message.get("role") or "")
            call_id = str(message.get("tool_call_id") or "")
            raw_name = message.get("name")
            thinking = str(message.get("thinking") or "")
            duration_ms = message.get("duration_ms")
            is_error = message.get("is_error", False)
        else:
            role = str(getattr(message, "role", "") or "")
            call_id = str(getattr(message, "tool_call_id", "") or "")
            raw_name = getattr(message, "name", None)
            thinking = str(getattr(message, "thinking", "") or "")
            duration_ms = getattr(message, "duration_ms", None)
            is_error = getattr(message, "is_error", False)
        _, content = _message_content(message)
        if role == "tool":
            name = str(raw_name or "") or tool_names.get(call_id, "")
            all_blocks.append({
                "id": f"b{index}",
                "kind": "tool",
                "role": role,
                "content": content,
                "call_id": call_id,
                "name": name,
                "args": tool_args.get(call_id, {}),
                "duration_ms": duration_ms,
                "is_error": bool(is_error),
            })
            continue
        if role != "user" and thinking.strip():
            all_blocks.append({
                "id": f"b{index}-t",
                "kind": "thinking",
                "role": role,
                "content": thinking,
            })
        if not content.strip():
            continue  # empty assistant separators between tool calls
        kind = "user" if role == "user" else "text"
        all_blocks.append({"id": f"b{index}", "kind": kind, "role": role, "content": content})
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
        as_dict = to_dict()
        if isinstance(as_dict, dict):
            return as_dict
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
