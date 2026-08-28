"""Session mutations shared by the TUI slash commands and the Web API."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apodex.session_state import load_session_state

if TYPE_CHECKING:
    from apodex.session import TerminalSession

HTTP_STATUS = {
    "ok": 200,
    "busy": 409,
    "validation": 400,
    "not_found": 404,
    "revision_conflict": 409,
}


class ActionError(Exception):
    def __init__(self, code: str, message: str, http_status: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.http_status = HTTP_STATUS.get(code, 500) if http_status is None else http_status


@dataclass
class ActionResult:
    ok: bool
    code: str
    message: str
    data: dict[str, Any]


class SessionActions:
    def __init__(self, session: TerminalSession) -> None:
        self.session = session

    def new_session(self, *, fork: bool = False) -> ActionResult:
        previous, current = self.session.start_new_session(fork=fork)
        if fork:
            message = f"saved {previous}\nforked context into {current}"
        else:
            message = f"saved {previous}\nstarted new session {current}"
        return ActionResult(
            True,
            "ok",
            message,
            {"previous": previous, "session_id": current},
        )

    def rename_session(self, name: str) -> ActionResult:
        try:
            clean = self.session.rename_session(name)
        except ValueError as exc:
            return ActionResult(False, "validation", str(exc), {})
        return ActionResult(True, "ok", f"session renamed → {clean}", {"name": clean})

    def resume_session(self, session_id: str) -> ActionResult:
        state = load_session_state(session_id)
        if state is None:
            return ActionResult(False, "not_found", "could not load that session", {})
        try:
            self.session.switch_session(state, fallback_id=session_id)
        except ValueError as exc:
            return ActionResult(False, "validation", str(exc), {})
        return ActionResult(
            True,
            "ok",
            f"resumed {self.session.session_id} ({len(self.session.history)} prior messages)",
            {"session_id": self.session.session_id},
        )

    def clear_context(self) -> ActionResult:
        session = self.session
        discarded_workspace = session._active_spill_workspace()
        session.history = []
        session.workflow_turns = []
        session.usage.clear_context()
        session._cleanup_discarded_spill(discarded_workspace)
        from apodex import fsguard
        from apodex.todo import clear_todos

        clear_todos()
        fsguard.clear()
        return ActionResult(
            True,
            "ok",
            "context + plan cleared (repo state unchanged)",
            {},
        )

    def revert_changes(self) -> ActionResult:
        observed = self.session.journal.observed_only()
        reverted = self.session.journal.revert_all()
        if reverted:
            message = "reverted " + ", ".join(reverted)
        elif observed:
            message = "nothing to revert (no attributed edits)"
        else:
            message = (
                "nothing to revert (no journaled edits — sandbox writes "
                "outside the session directory are not tracked)"
            )
        if observed:
            message += (
                "\nleft alone (found by scanning, not attributed to a file "
                "tool — undo them yourself): " + ", ".join(observed)
            )
        return ActionResult(
            True,
            "ok",
            message,
            {"reverted": reverted, "observed_only": observed},
        )
