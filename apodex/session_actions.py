"""Session mutations shared by the TUI slash commands and the Web API."""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from apodex.session_state import list_saved_sessions, load_session_state

if TYPE_CHECKING:
    from apodex.commands import CommandSpec
    from apodex.session import TerminalSession

HTTP_STATUS = {
    "ok": 200,
    "busy": 409,
    "validation": 400,
    "not_found": 404,
    "revision_conflict": 409,
    "dangerous_confirmation": 400,
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

    def switch_workflow(self, name: str) -> ActionResult:
        from apodex.profiles import get_profile, terminal_mode_names

        session = self.session
        available = terminal_mode_names()
        if not name:
            return ActionResult(
                True,
                "ok",
                f"mode = {session.mode}  (available: {', '.join(available)})",
                {"mode": session.mode},
            )
        if name not in available:
            return ActionResult(
                False,
                "validation",
                f"unknown mode {name!r}; available: {', '.join(available)}",
                {},
            )
        import dataclasses

        from apodex.config import format_preflight_errors
        from apodex.env import environment_section
        from apodex.llm import build_llm
        from apodex.middleware import _wrap_skills_llm
        from apodex.todo import clear_todos

        new_profile = get_profile(name)
        new_cfg = dataclasses.replace(new_profile.model_config)
        status = new_profile.runtime_config(new_cfg, mode=name)
        if not status.ok:
            return ActionResult(False, "validation", format_preflight_errors(status), {})
        new_env_section = environment_section(session.cwd, new_cfg.model)
        new_llm = build_llm(new_cfg)
        if new_profile.skills:
            new_llm = _wrap_skills_llm(new_llm, new_profile.skills)

        discarded_workspace = session._active_spill_workspace()
        session.mode = name
        session.history = []
        session.workflow_turns = []
        session.usage.clear_context()
        session._cleanup_discarded_spill(discarded_workspace)
        clear_todos()
        session.cfg = new_cfg
        session.models = list(new_profile.models)
        session.r.set_usage(session.usage, session.cfg.context_window)
        session._env_section = new_env_section
        session.llm = new_llm
        message = f"mode → {name} (model {session.cfg.model}, context reset)"
        warnings = "\n".join(f"warning: {warning.message}" for warning in status.warnings)
        if warnings:
            message = f"{message}\n{warnings}"
        return ActionResult(True, "ok", message, {"mode": name, "model": session.cfg.model})

    def switch_model(self, target: str) -> ActionResult:
        session = self.session
        models = list(getattr(session, "models", None) or [])
        if not target:
            if models:
                lines = "\n".join(
                    f"  {i}. {m}" + ("  (current)" if m == session.cfg.model else "")
                    for i, m in enumerate(models, 1)
                )
                message = (
                    f"model = {session.cfg.model}\n"
                    f"available (profile '{session.mode}'):\n{lines}\n"
                    f"switch with /model <name|number>"
                )
            else:
                message = f"model = {session.cfg.model}"
            return ActionResult(True, "ok", message, {"model": session.cfg.model})
        name = target
        if target.isdigit() and 1 <= int(target) <= len(models):
            name = models[int(target) - 1]
        try:
            from apodex.env import environment_section

            session.cfg.model = name
            session._build_llm()
            session._env_section = environment_section(session.cwd, session.cfg.model)
        except Exception as exc:
            return ActionResult(False, "validation", f"model switch failed: {exc}", {})
        note = f"model → {name}"
        if models and name not in models:
            note += " (not in the profile's list)"
        return ActionResult(True, "ok", note, {"model": name})

    def change_cwd(self, path: str) -> ActionResult:
        session = self.session
        if not path:
            return ActionResult(True, "ok", f"cwd = {session.cwd}", {"cwd": session.cwd})
        try:
            discarded_workspace = session._active_spill_workspace()
            os.chdir(path)
            session.cwd = os.getcwd()
            manager = getattr(session, "attachments", None)
            update_root = getattr(manager, "set_source_root", None)
            if callable(update_root):
                update_root(session.cwd)
            session._activate_session_workspace(session.session_id, session.cwd)
            session._activate_session_outputs(session.session_id, session.cwd)
            session._authorize_workspace(session.cwd)
            session.history = []
            session.workflow_turns = []
            session.usage.clear_context()
            session._cleanup_discarded_spill(discarded_workspace)
            from apodex import fsguard
            from apodex.changes import WorkspaceJournal
            from apodex.env import environment_section

            session.journal = WorkspaceJournal(session.cwd)
            fsguard.clear()
            session._env_section = environment_section(session.cwd, session.cfg.model)
        except Exception as exc:
            return ActionResult(False, "validation", f"cd failed: {exc}", {})
        return ActionResult(True, "ok", f"cwd → {session.cwd} (context reset)", {"cwd": session.cwd})

    async def compact_context(self) -> ActionResult:
        session = self.session
        if not session.history:
            return ActionResult(True, "ok", "nothing to compact (no conversation yet)", {})
        from frontier_agent.core.runtime.loop.compact_llm import LLMSummaryCompactor

        before = len(session.history)
        tool_results = sum(message.get("role") == "tool" for message in session.history)
        session.history = await LLMSummaryCompactor(summary_llm=session.llm).compact(
            session.history,
            keep_recent=0,
            compress_all_tool_results=True,
        )
        session.usage.compactions += 1
        return ActionResult(
            True,
            "ok",
            (
                f"compacted {tool_results} tool results and summarized history: "
                f"{before} → {len(session.history)} messages"
            ),
            {"before": before, "after": len(session.history)},
        )

    def set_plan_mode(self, active: bool) -> ActionResult:
        self.session.plan_state.active = active
        if active:
            message = "▤ plan mode ON — edits locked until you approve a plan"
        else:
            message = "plan mode OFF — edits allowed (with approval)"
        return ActionResult(True, "ok", message, {"active": active})

    def set_verbose(self, enabled: bool) -> ActionResult:
        self.session.verbose = enabled
        self.session.r.set_verbose(enabled)
        return ActionResult(True, "ok", f"verbose thinking = {enabled}", {"verbose": enabled})

    def set_auto_approve(self, enabled: bool) -> ActionResult:
        self.session.approver.auto_approve = enabled
        self.session.user_settings.auto_approve = enabled
        self.session.user_settings.save()
        return ActionResult(
            True,
            "ok",
            f"bypass permission (auto-approve) = {enabled}",
            {"auto_approve": enabled},
        )

    def set_auto_for_me(self, enabled: bool) -> ActionResult:
        self.session.approver.auto_for_me = enabled
        self.session.user_settings.auto_for_me = enabled
        self.session.user_settings.save()
        return ActionResult(
            True,
            "ok",
            f"auto for me (docker/trusted env mode) = {enabled}",
            {"auto_for_me": enabled},
        )

    def set_theme(self, name: str) -> ActionResult:
        from apodex.tui.themes import CLI_THEME_NAMES

        cleaned = (name or "").strip()
        if cleaned not in CLI_THEME_NAMES:
            return ActionResult(
                False,
                "validation",
                "unknown theme: " + cleaned,
                {"themes": list(CLI_THEME_NAMES)},
            )
        self.session.user_settings.theme = cleaned
        self.session.user_settings.save()
        return ActionResult(True, "ok", f"theme → {cleaned}", {"theme": cleaned})

    def attach_paths(self, argument: str) -> ActionResult:
        from apodex.attachments import AttachmentError

        try:
            paths = shlex.split(argument)
        except ValueError as exc:
            return ActionResult(False, "validation", str(exc), {})
        if not paths:
            return ActionResult(False, "validation", "usage: /attach <path> [path ...]", {})
        try:
            added = self.session.attachments.attach_many(paths)
        except (AttachmentError, ValueError) as exc:
            return ActionResult(False, "validation", str(exc), {})
        return ActionResult(
            True,
            "ok",
            "attached: " + ", ".join(item.relative_path for item in added),
            {"paths": [item.relative_path for item in added]},
        )

    def list_attachments(self) -> ActionResult:
        from apodex.attachments import format_size

        items = self.session.attachments.list()
        message = "no files attached" if not items else "attached files:\n" + "\n".join(
            f"  {item.relative_path} · {format_size(item.size)} · {item.agent_path}"
            for item in items
        )
        return ActionResult(
            True,
            "ok",
            message,
            {"items": [{"relative_path": item.relative_path, "size": item.size} for item in items]},
        )

    def detach_attachment(self, argument: str) -> ActionResult:
        from apodex.attachments import AttachmentError

        target = argument.strip()
        if not target:
            return ActionResult(False, "validation", "usage: /detach <attachment>", {})
        try:
            removed = self.session.attachments.detach(target)
        except (AttachmentError, ValueError) as exc:
            return ActionResult(False, "validation", str(exc), {})
        return ActionResult(
            True,
            "ok",
            f"detached {target} ({removed} file(s))",
            {"removed": removed},
        )

    def runtime_config(self) -> ActionResult:
        from apodex.config import format_runtime_config_status

        status = self.session.runtime_config_status()
        return ActionResult(True, "ok", format_runtime_config_status(status), {})

    def context_cost(self) -> ActionResult:
        session = self.session
        message = session.usage.context_report(
            session.cfg.context_window,
            output_reserve=int(getattr(session.cfg, "max_tokens", 0) or 0),
        )
        return ActionResult(True, "ok", message, {})

    def trace_path(self) -> ActionResult:
        path = getattr(self.session, "trace_path", "")
        return ActionResult(True, "ok", f"trace: {path}", {"path": str(path)})

    def list_sessions(self) -> ActionResult:
        sessions = list_saved_sessions()[:20]
        if not sessions:
            return ActionResult(True, "ok", "no saved sessions yet", {"sessions": []})
        rows = ["Recent sessions:"]
        for item in sessions:
            label = f" · {item['name']}" if item.get("name") else ""
            current = "  (current)" if item["session_id"] == self.session.session_id else ""
            rows.append(
                f"  {item['session_id']}{label}{current}\n"
                f"    {item['modified_at']} · {item['mode']} · "
                f"{item['message_count']} msgs · {item['cwd']}"
            )
        return ActionResult(True, "ok", "\n".join(rows), {"sessions": sessions})

    async def dispatch(self, spec: CommandSpec, argument: str) -> ActionResult:
        action = spec.action
        if action == "new_session":
            return self.new_session(fork=False)
        if action == "fork_session":
            return self.new_session(fork=True)
        if action == "clear_context":
            return self.clear_context()
        if action == "revert_changes":
            return self.revert_changes()
        if action == "rename_session":
            return self.rename_session(argument)
        if action == "resume_session":
            return self.resume_session(argument)
        if action == "switch_workflow":
            return self.switch_workflow(argument)
        if action == "switch_model":
            return self.switch_model(argument)
        if action == "change_cwd":
            return self.change_cwd(argument)
        if action == "compact_context":
            return await self.compact_context()
        if action == "toggle_plan_mode":
            return self.set_plan_mode(not self.session.plan_state.active)
        if action == "toggle_verbose":
            return self.set_verbose(not self.session.verbose)
        if action == "toggle_auto_approve":
            return self.set_auto_approve(not self.session.approver.auto_approve)
        if action == "toggle_auto_for_me":
            return self.set_auto_for_me(not self.session.approver.auto_for_me)
        if action == "attach_paths":
            return self.attach_paths(argument)
        if action == "list_attachments":
            return self.list_attachments()
        if action == "detach_attachment":
            return self.detach_attachment(argument)
        if action == "runtime_config":
            return self.runtime_config()
        if action == "context_cost":
            return self.context_cost()
        if action == "trace_path":
            return self.trace_path()
        if action == "list_sessions":
            return self.list_sessions()
        if action == "run_init":
            return ActionResult(True, "ok", "", {})
        return ActionResult(False, "validation", f"unknown action {action!r}", {})
