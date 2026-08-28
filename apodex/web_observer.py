"""WebObserver, WebApprover, and WebRenderer for FrontierAgent.

Bridges the FrontierAgent loop, ReAct workflows, and Agent Team coordination
to real-time Server-Sent Events (SSE) and HTTP REST APIs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from apodex.agent_tools import (
    MUTATING_TOOLS,
    RISK_DENY,
    RISK_SAFE,
    assess_with_rules,
    is_mutating_tool,
    localize_path_args,
)
from apodex.diff_preview import change_stats, unified_diff
from apodex.observers import Decision
from apodex.render import Renderer
from frontier_agent.core.loop_types import (
    BaseObserver,
    Intervention,
    LLMDeltaContext,
    LoopConfig,
    ToolCallIntervention,
    ToolResult,
    TurnContext,
)

logger = logging.getLogger(__name__)

_DIFF_TOOLS = frozenset({"write_file", "file_editor_create", "file_editor_str_replace"})


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
        approved: bool,
        feedback: str = "",
        remember: bool = False,
        auto_all: bool = False,
    ) -> bool:
        fut = self._pending.get(approval_id)
        if fut is None or fut.done():
            return False
        if auto_all:
            self.auto_approve = True
        fut.set_result(Decision(approved=approved, feedback=feedback, remember=remember))
        return True


class WebRenderer(Renderer):
    """Renderer implementation that forwards all session output to the SSE broadcaster."""

    def __init__(self, broadcaster: EventBroadcaster) -> None:
        super().__init__(theme="catppuccin", color=False, verbose=True)
        self.broadcaster = broadcaster
        self._current_thinking = ""
        self._current_content = ""

    def _sync_emit(self, event_type: str, data: dict[str, Any]) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(self.broadcaster.emit(event_type, data))
        except RuntimeError:
            pass

    def set_usage(self, usage: Any, window: int) -> None:
        super().set_usage(usage, window)
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
        self._sync_emit("status", {"state": "thinking", "message": message})

    def working_off(self) -> None:
        pass

    def thinking_delta(self, delta: str) -> None:
        self._current_thinking += delta
        self._sync_emit("thinking_delta", {"delta": delta, "accumulated": self._current_thinking})

    def content_delta(self, delta: str) -> None:
        self._current_content += delta
        self._sync_emit("content_delta", {"delta": delta, "accumulated": self._current_content})

    def turn_text_fallback(self, text: str, thinking: str = "") -> None:
        self._current_content = text
        self._current_thinking = thinking
        self._sync_emit("turn_fallback", {"text": text, "thinking": thinking})

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

    def tool_result(
        self,
        name: str,
        result: Any,
        *,
        call_id: str = "",
        is_error: bool = False,
        ms: int = 0,
    ) -> None:
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

    def diff_preview(self, diff: str, *, stats: Any = None) -> None:
        self._sync_emit("diff_preview", {"diff": diff, "stats": str(stats) if stats else ""})

    def note(self, text: str) -> None:
        self._sync_emit("note", {"text": text})

    def error(self, text: str) -> None:
        self._sync_emit("error", {"message": text})

    def final(self, text: str, *, turns: int = 0, tool_calls: int = 0, stopped_by: str = "") -> None:
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

    def changes(self, stats: Any) -> None:
        self._sync_emit("changes_summary", {"stats": stats})

    def subagent_status(
        self,
        snapshots: list[dict[str, Any]],
        *,
        done: bool = False,
        timeout_s: int = 0,
    ) -> None:
        self._sync_emit(
            "subagent_status",
            {"snapshots": snapshots, "done": done, "timeout_s": timeout_s},
        )


class WebObserver(BaseObserver):
    """Observer hooking into `run_agent_loop` and Agent Team workflows."""

    critical = True
    wants_llm_delta = True

    def __init__(
        self,
        broadcaster: EventBroadcaster,
        approver: WebApprover,
        cwd: str,
        journal: Any = None,
        plan_state: Any = None,
        steer_inbox: Any = None,
        rules: Any = None,
    ) -> None:
        self.broadcaster = broadcaster
        self.approver = approver
        self.cwd = cwd
        self.journal = journal
        self.plan_state = plan_state
        self.steer_inbox = steer_inbox
        self.rules = rules

        self._activity_sequence = 0
        self._turn_streamed = False
        self._current_thinking = ""
        self._current_content = ""
        self._journal_scan: tuple[list[str], Any] | None = None
        self._journal_scan_lock = asyncio.Lock()

    async def on_loop_start(self, config: LoopConfig) -> None:
        await self.broadcaster.emit(
            "status",
            {"state": "running", "role": config.role_id, "max_turns": config.max_turns},
        )

    async def on_llm_delta(self, ctx: LLMDeltaContext) -> Intervention | None:
        self._turn_streamed = True
        thinking_delta = getattr(ctx, "thinking_delta", "")
        if thinking_delta:
            self._current_thinking += thinking_delta
            await self.broadcaster.emit(
                "thinking_delta",
                {"delta": thinking_delta, "accumulated": self._current_thinking},
            )
        if ctx.delta:
            self._current_content += ctx.delta
            await self.broadcaster.emit(
                "content_delta",
                {"delta": ctx.delta, "accumulated": self._current_content},
            )
        return None

    async def on_llm_response(self, ctx: TurnContext) -> Intervention | None:
        if not self._turn_streamed:
            await self.broadcaster.emit(
                "turn_fallback",
                {"text": ctx.ai_text or "", "thinking": ctx.thinking or ""},
            )
        else:
            await self.broadcaster.emit(
                "turn_complete",
                {"text": self._current_content, "thinking": self._current_thinking},
            )
        self._turn_streamed = False
        self._current_thinking = ""
        self._current_content = ""
        return None

    async def on_tool_call(
        self, ctx: TurnContext, tool_call: dict[str, Any],
    ) -> ToolCallIntervention | None:
        name = tool_call.get("name", "")
        args = tool_call.get("args", {}) or {}
        self._activity_sequence += 1
        call_id = str(tool_call.get("id") or f"web-{ctx.turn}-{self._activity_sequence}")

        rewritten = localize_path_args(name, args, self.cwd)
        eff_args = rewritten if rewritten is not None else args

        risk = assess_with_rules(
            name,
            eff_args,
            self.cwd,
            self.rules,
            auto_for_me=getattr(self.approver, "auto_for_me", False),
        )

        preview = ""
        preview_kind = ""
        if name in _DIFF_TOOLS:
            diff = unified_diff(name, eff_args, self.cwd)
            if diff:
                preview, preview_kind = diff, "diff"
                await self.broadcaster.emit("diff_preview", {"diff": diff, "target": name})
        elif name == "bash":
            cmd = str(eff_args.get("command", "")).strip()
            if cmd:
                preview, preview_kind = cmd, "command"

        await self.broadcaster.emit(
            "tool_call",
            {
                "call_id": call_id,
                "name": name,
                "args": eff_args,
                "risk_reason": risk.danger or ("" if risk.level == RISK_SAFE else risk.reason),
                "danger": bool(risk.danger),
                "preview": preview,
                "preview_kind": preview_kind,
                "status": "running",
            },
        )

        if risk.level == RISK_DENY:
            await self.broadcaster.emit(
                "note", {"text": f"✗ blocked by policy: {risk.reason}"}
            )
            return ToolCallIntervention(
                skip_with_result=f"[blocked by safety policy: {risk.reason}]"
            )

        if risk.level != RISK_SAFE:
            decision = await self.approver.confirm(
                name,
                risk.target,
                risk.reason,
                dangerous=risk.danger,
                preview=preview,
                preview_kind=preview_kind,
            )
            if not decision.approved:
                if decision.feedback:
                    await self.broadcaster.emit(
                        "note", {"text": f"↳ redirecting {name}: {decision.feedback}"}
                    )
                    return ToolCallIntervention(
                        skip_with_result=(
                            f"[The user declined to run this {name} call. "
                            f"Follow their instruction instead: {decision.feedback}]"
                        )
                    )
                await self.broadcaster.emit(
                    "note", {"text": f"✗ rejected {name} by user"}
                )
                return ToolCallIntervention(
                    skip_with_result=f"[user rejected this {name} call]"
                )
            if decision.remember and self.rules is not None:
                self.rules.allow_command(risk.target)

        return None

    async def on_tool_result(self, ctx: TurnContext, result: ToolResult) -> None:
        await self.broadcaster.emit(
            "tool_result",
            {
                "call_id": getattr(result, "call_id", f"res-{self._activity_sequence}"),
                "name": result.name,
                "result": str(result.result)[:4000],
                "is_error": result.is_error,
                "duration_ms": result.duration_ms,
                "status": "error" if result.is_error else "completed",
            },
        )

    def on_subagent_status(
        self,
        snapshots: list[dict[str, Any]],
        *,
        done: bool = False,
        timeout_s: int = 0,
    ) -> None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(
                self.broadcaster.emit(
                    "subagent_status",
                    {"snapshots": snapshots, "done": done, "timeout_s": timeout_s},
                )
            )
        except RuntimeError:
            pass
