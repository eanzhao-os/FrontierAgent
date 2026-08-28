"""WebApprover and WebRenderer for FrontierAgent.

Bridges session rendering and human-in-the-loop approvals to Server-Sent Events.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from apodex.observers import Decision
from apodex.render import Renderer


@dataclass
class WebEvent:
    event_type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)
    sequence: int = 0

    def to_sse(self) -> str:
        payload = json.dumps(
            {"type": self.event_type, "data": self.data, "timestamp": self.timestamp},
            ensure_ascii=False,
        )
        return f"id: {self.sequence}\nevent: {self.event_type}\ndata: {payload}\n\n"


class EventBroadcaster:
    """Thread-safe and async broadcast hub for streaming events to SSE clients."""

    def __init__(self, max_history: int = 500) -> None:
        self._subscribers: set[asyncio.Queue[WebEvent]] = set()
        self._history: deque[WebEvent] = deque(maxlen=max_history)
        self._lock = asyncio.Lock()
        self._sequence = 0

    @property
    def sequence(self) -> int:
        return self._sequence

    def replay_after(self, last_id: int) -> list[WebEvent] | None:
        if self._history:
            oldest = self._history[0].sequence
            if last_id < oldest:
                return None
            return [event for event in self._history if event.sequence > last_id]
        if last_id < self._sequence:
            return None
        return []

    async def subscribe(self, last_id: int | None = None) -> asyncio.Queue[WebEvent]:
        q: asyncio.Queue[WebEvent] = asyncio.Queue()
        async with self._lock:
            if last_id is not None:
                replayed = self.replay_after(last_id)
                if replayed is not None:
                    for ev in replayed:
                        await q.put(ev)
            self._subscribers.add(q)
        return q

    async def unsubscribe(self, q: asyncio.Queue[WebEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    async def emit(self, event_type: str, data: dict[str, Any]) -> WebEvent:
        async with self._lock:
            self._sequence += 1
            event = WebEvent(event_type=event_type, data=data, sequence=self._sequence)
            self._history.append(event)
            dead: list[asyncio.Queue[WebEvent]] = []
            for q in self._subscribers:
                try:
                    q.put_nowait(event)
                except asyncio.QueueFull:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)
        return event

    def clear(self) -> None:
        self._history.clear()


class WebApprover:
    """Human-in-the-loop approver that suspends execution until a Web client submits a decision."""

    def __init__(self, broadcaster: EventBroadcaster, *, auto_approve: bool = False) -> None:
        self.broadcaster = broadcaster
        self.auto_approve = auto_approve
        self.auto_for_me = False
        self.interactive = True
        self.inbox: Any = None
        self._pending: dict[str, asyncio.Future[Decision]] = {}
        self._pending_info: dict[str, dict[str, Any]] = {}

    def pending_snapshot(self) -> dict[str, Any] | None:
        for approval_id, fut in self._pending.items():
            if fut.done():
                continue
            info = self._pending_info.get(approval_id)
            if info is not None:
                return {
                    "id": info["id"],
                    "tool": info["tool"],
                    "target": info["target"],
                    "reason": info["reason"],
                    "dangerous": info["dangerous"],
                    "preview": info["preview"],
                    "preview_kind": info["preview_kind"],
                }
        return None

    async def confirm(
        self,
        name: str,
        target: str,
        reason: str,
        *,
        dangerous: str = "",
        preview: str = "",
        preview_kind: str = "",
    ) -> Decision:
        if self.auto_approve:
            return Decision(True)

        approval_id = f"appr-{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Decision] = loop.create_future()
        info = {
            "id": approval_id,
            "tool": name,
            "target": target,
            "reason": reason,
            "dangerous": dangerous,
            "preview": preview,
            "preview_kind": preview_kind,
        }
        self._pending[approval_id] = fut
        self._pending_info[approval_id] = info

        # Broadcast approval request to frontend
        await self.broadcaster.emit(
            "approval_required",
            {**info, "timestamp": time.time()},
        )

        try:
            decision = await fut
            return decision
        finally:
            self._pending.pop(approval_id, None)
            self._pending_info.pop(approval_id, None)

    def resolve(
        self,
        approval_id: str,
        *,
        decision: str,
        feedback: str = "",
        confirmation: str = "",
    ) -> bool:
        fut = self._pending.get(approval_id)
        if fut is None or fut.done():
            return False
        info = self._pending_info.get(approval_id) or {}
        dangerous = str(info.get("dangerous") or "").strip()
        if decision == "approve" and dangerous and confirmation != "yes":
            return False
        approved = decision in {"approve", "auto_for_me", "allow_session", "always_allow"}
        remember = decision == "always_allow"
        if decision == "allow_session":
            self.auto_approve = True
        if decision == "auto_for_me":
            self.auto_for_me = True
        fut.set_result(Decision(approved=approved, feedback=feedback, remember=remember))
        return True


class WebRenderer(Renderer):
    """Renderer implementation that forwards all session output to the SSE broadcaster."""

    def __init__(self, broadcaster: EventBroadcaster) -> None:
        super().__init__(theme="catppuccin", color=False, verbose=True)
        self.broadcaster = broadcaster
        self._current_thinking = ""
        self._current_content = ""
        self._emit_tasks: set[asyncio.Task[Any]] = set()
        self.phase = "idle"
        self.queued_count = 0
        self.tool_count = 0
        self.current_tool = ""
        self.elapsed_seconds = None
        self._task_started_at: float | None = None
        self.activity: list[dict[str, Any]] = []
        self.activity_totals: dict[str, int] = {"calls": 0, "success": 0, "failed": 0}
        self.todo_items: list[dict[str, str]] = []
        self.plan_text = ""
        self.plan_items: list[dict[str, Any]] = []
        self.subagents: list[dict[str, Any]] = []

    def presentation_state(self) -> dict[str, Any]:
        elapsed = None
        if self._task_started_at is not None:
            elapsed = int(time.time() - self._task_started_at)
        self.elapsed_seconds = elapsed
        return {
            "phase": self.phase,
            "elapsed_seconds": elapsed,
            "tool_count": self.tool_count,
            "queued": self.queued_count,
            "current_tool": self.current_tool,
        }

    def _sync_emit(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        self._emit_tasks.add(loop.create_task(self.broadcaster.emit(event_type, data)))

    def _set_phase(self, phase: str, *, tool: str | None = None) -> None:
        changed = self.phase != phase
        self.phase = phase
        if tool is not None and self.current_tool != tool:
            self.current_tool = tool
            changed = True
        if self._task_started_at is None and phase not in {"idle", "done", "incomplete", "interrupted", "error"}:
            self._task_started_at = time.time()
            changed = True
        if changed:
            self._sync_emit("presentation", self.presentation_state())

    def _append_activity(self, record: dict[str, Any]) -> None:
        self.activity.append(record)
        if len(self.activity) > 100:
            self.activity = self.activity[-100:]

    def set_usage(self, usage: Any, window: int) -> None:
        self._usage = usage
        self._sync_emit(
            "usage",
            {
                "prompt_tokens": getattr(usage, "prompt_tokens", 0),
                "completion_tokens": getattr(usage, "completion_tokens", 0),
                "total_tokens": getattr(usage, "total_tokens", 0),
                "window": window,
            },
        )

    def working_on(self, message: str = "Thinking...") -> None:
        self._set_phase("thinking")
        self._sync_emit("status", {"state": "thinking", "message": message})

    def working_off(self) -> None:
        return

    def thinking_delta(self, s: str) -> None:
        self._current_thinking += s
        self._set_phase("thinking")
        self._sync_emit("thinking_delta", {"delta": s, "accumulated": self._current_thinking})

    def content_delta(self, s: str) -> None:
        self._current_content += s
        self._set_phase("responding")
        self._sync_emit("content_delta", {"delta": s, "accumulated": self._current_content})

    def turn_text_fallback(self, ai_text: str, thinking: str) -> None:
        self._current_content = ai_text
        self._current_thinking = thinking
        self._sync_emit("turn_fallback", {"text": ai_text, "thinking": thinking})

    def end_turn_text(self) -> None:
        self._sync_emit(
            "turn_complete",
            {"text": self._current_content, "thinking": self._current_thinking},
        )
        self._current_thinking = ""
        self._current_content = ""

    def tool_call(
        self,
        name: str,
        args: dict[str, Any],
        risk_reason: str = "",
        danger: bool = False,
        *,
        call_id: str = "",
    ) -> None:
        from apodex.task_projection import project_task_board

        self.tool_count += 1
        self._set_phase("running_tool", tool=name)
        self._sync_emit("presentation", self.presentation_state())
        self._sync_emit(
            "tool_call",
            {
                "call_id": call_id,
                "name": name,
                "args": args,
                "risk_reason": risk_reason,
                "danger": danger,
                "status": "executing",
            },
        )
        if name in {"add_task", "update_task"}:
            self.plan_items = project_task_board(name, args or {}, self.plan_items)
            self._sync_emit("plan", {"items": self.plan_items})

    def tool_result(
        self,
        name: str,
        result: Any,
        *,
        call_id: str = "",
        is_error: bool = False,
        ms: int = 0,
    ) -> None:
        self._set_phase("thinking", tool="")
        self._sync_emit(
            "tool_result",
            {
                "call_id": call_id,
                "name": name,
                "result": str(result),
                "is_error": is_error,
                "duration_ms": ms,
                "status": "error" if is_error else "completed",
            },
        )

    def activity_call(self, name: str, args: dict, *, call_id: str = "") -> None:
        record = {
            "call_id": call_id,
            "name": name,
            "args": args,
            "state": "running",
        }
        self.activity_totals["calls"] += 1
        self._append_activity(record)
        self._sync_emit("activity_call", record)

    def activity_result(
        self,
        name: str,
        result: Any = "",
        *,
        call_id: str = "",
        is_error: bool = False,
        ms: int = 0,
        outcome: str = "",
    ) -> None:
        state = "failed" if is_error else "success"
        self.activity_totals["failed" if is_error else "success"] += 1
        payload = {
            "call_id": call_id,
            "name": name,
            "result": result,
            "is_error": is_error,
            "ms": ms,
            "outcome": outcome,
            "state": state,
        }
        for rec in reversed(self.activity):
            if rec.get("call_id") == call_id or (
                not call_id and rec.get("name") == name and rec.get("state") == "running"
            ):
                rec["state"] = state
                rec["ms"] = ms
                rec["is_error"] = is_error
                break
        self._sync_emit("activity_result", payload)

    def todos(self, items: list) -> None:
        from apodex.task_projection import project_todos

        self.todo_items = project_todos(items)
        self._sync_emit("todos", {"items": self.todo_items})

    def plan_review(self, plan: str) -> None:
        self.plan_text = plan
        self._set_phase("awaiting_approval")
        self._sync_emit("plan_review", {"plan": plan})

    def queued(self, text: str) -> None:
        self.queued_count += 1
        self._sync_emit("presentation", self.presentation_state())
        self._sync_emit("queued", {"text": text})

    def llm_failure(self, msg: str, *, configuration_error: bool = False) -> None:
        self._set_phase("error")
        self._sync_emit(
            "llm_failure",
            {"message": msg, "configuration_error": configuration_error},
        )

    def diff_preview(self, diff_text: str, *, stats: tuple[int, int] | None = None) -> None:
        self._sync_emit("diff_preview", {"diff": diff_text, "stats": str(stats) if stats else ""})

    def note(self, msg: str) -> None:
        self._sync_emit("note", {"text": msg})

    def error(self, msg: str) -> None:
        self._set_phase("error")
        self._sync_emit("error", {"message": msg})

    def final(self, text: str, *, turns: int = 0, tool_calls: int = 0, stopped_by: str = "") -> None:
        self._set_phase("done")
        self._sync_emit(
            "final_answer",
            {
                "text": text,
                "turns": turns,
                "tool_calls": tool_calls,
                "stopped_by": stopped_by,
                "status": "completed",
            },
        )

    def incomplete(self, text: str, *, turns: int = 0, tool_calls: int = 0, stopped_by: str = "") -> None:
        phase = "interrupted" if stopped_by == "interrupt" else "incomplete"
        self._set_phase(phase)
        self._sync_emit(
            "final_answer",
            {
                "text": text,
                "turns": turns,
                "tool_calls": tool_calls,
                "stopped_by": stopped_by,
                "status": "incomplete",
            },
        )

    def interrupted(self) -> None:
        for rec in self.activity:
            if rec.get("state") == "running":
                rec["state"] = "interrupted"
        self._set_phase("interrupted")

    def changes(self, stats: Any) -> None:
        self._sync_emit("changes_summary", {"stats": stats})

    def subagent_status(
        self,
        snapshots: list[dict[str, Any]],
        *,
        done: bool = False,
        timeout_s: int = 0,
    ) -> None:
        self.subagents = list(snapshots)
        self._sync_emit(
            "subagent_status",
            {"snapshots": snapshots, "done": done, "timeout_s": timeout_s},
        )
