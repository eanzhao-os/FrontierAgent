"""Backend-neutral ReAct todo and Agent Team board coercion."""

from __future__ import annotations

from typing import Any

from plugins.tools._coerce import coerce_json_list


def project_todos(items: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for entry in coerce_json_list(items) or []:
        if isinstance(entry, dict):
            content = str(entry.get("content") or entry.get("task") or "").strip()
            status = str(entry.get("status") or "pending").strip().lower()
        elif isinstance(entry, str):
            content, status = entry.strip(), "pending"
        else:
            content = str(getattr(entry, "content", "") or "").strip()
            status = str(getattr(entry, "status", "pending") or "pending").strip().lower()
        if not content:
            continue
        if status not in {"pending", "in_progress", "completed"}:
            status = "pending"
        out.append({"content": content, "status": status})
    return out


def project_task_board(tool_name: str, args: dict, previous: list[dict]) -> list[dict]:
    if tool_name == "add_task":
        rows = coerce_json_list(args.get("tasks")) or []
        out: list[dict] = []
        for i, row in enumerate(rows, 1):
            if not isinstance(row, dict):
                continue
            desc = str(row.get("description") or "").strip()
            if not desc:
                continue
            out.append({
                "id": str(row.get("id") or f"t{i}"),
                "description": desc,
                "status": str(row.get("status") or "open"),
                "owner": str(row.get("assigned_agent") or row.get("owner") or ""),
            })
        return out
    if tool_name == "update_task":
        updates = coerce_json_list(args.get("updates")) or []
        by_id = {item["id"]: dict(item) for item in previous}
        for row in updates:
            if not isinstance(row, dict):
                continue
            tid = str(row.get("id") or "")
            if tid not in by_id:
                continue
            current = by_id[tid]
            if "status" in row:
                current["status"] = str(row["status"])
            if "description" in row:
                current["description"] = str(row["description"])
            if "assigned_agent" in row or "owner" in row:
                current["owner"] = str(row.get("assigned_agent") or row.get("owner") or "")
        return list(by_id.values())
    return previous
