"""Canonical slash-command registry shared by the TUI and Web capabilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

CommandKind = Literal["session_action", "presentation", "task_submit"]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    aliases: tuple[str, ...]
    description: str
    argument_hint: str
    argument_required: bool
    available_when_busy: bool
    kind: CommandKind
    action: str | None
    shortcuts: tuple[str, ...]
    browser_shortcuts: tuple[str, ...]


def _command(
    name: str,
    description: str,
    kind: CommandKind,
    action: str | None = None,
    *,
    aliases: tuple[str, ...] = (),
    argument_hint: str = "",
    argument_required: bool = False,
) -> CommandSpec:
    return CommandSpec(
        name=name,
        aliases=aliases,
        description=description,
        argument_hint=argument_hint,
        argument_required=argument_required,
        available_when_busy=False,
        kind=kind,
        action=action,
        shortcuts=(),
        browser_shortcuts=(),
    )


COMMANDS: tuple[CommandSpec, ...] = (
    _command("/help", "shortcuts and interaction help", "presentation"),
    _command(
        "/mode",
        "switch coding, research, React, or Agent Team workflow",
        "session_action",
        "switch_workflow",
        argument_hint="<workflow>",
    ),
    _command("/model", "select a model", "session_action", "switch_model", argument_hint="<model>"),
    _command("/settings", "open theme and workflow settings", "presentation", aliases=("/menu",)),
    _command(
        "/cwd",
        "show or change working directory",
        "session_action",
        "change_cwd",
        argument_hint="[path]",
    ),
    _command("/clear", "clear conversation context and plan", "session_action", "clear_context"),
    _command(
        "/new",
        "save this session and start a fresh one",
        "session_action",
        "new_session",
    ),
    _command(
        "/fork",
        "branch the current context into a new session",
        "session_action",
        "fork_session",
    ),
    _command("/sessions", "list recent saved sessions", "session_action", "list_sessions"),
    _command(
        "/rename",
        "give the current session a readable name",
        "session_action",
        "rename_session",
        argument_hint="<name>",
        argument_required=True,
    ),
    _command("/plan", "toggle plan mode", "session_action", "toggle_plan_mode"),
    _command("/revert", "undo session file changes", "session_action", "revert_changes"),
    _command("/compact", "summarize earlier turns", "session_action", "compact_context"),
    _command("/cost", "show token and context usage", "session_action", "context_cost"),
    _command(
        "/context",
        "visualize current context usage",
        "session_action",
        "context_cost",
    ),
    _command(
        "/config",
        "show safe local runtime configuration",
        "session_action",
        "runtime_config",
    ),
    _command("/init", "write or improve AGENTS.md", "task_submit", "run_init"),
    _command("/resume", "continue a saved session", "session_action", "resume_session"),
    _command("/log", "show local trace path", "session_action", "trace_path"),
    _command("/auto", "toggle auto-approval", "session_action", "toggle_auto_approve"),
    _command(
        "/bypass",
        "bypass all permission checks",
        "session_action",
        "toggle_auto_approve",
    ),
    _command(
        "/autome",
        "auto-approve for me (docker / trusted env)",
        "session_action",
        "toggle_auto_for_me",
        aliases=("/auto-for-me",),
    ),
    _command("/verbose", "toggle thinking details", "session_action", "toggle_verbose"),
    _command(
        "/filter",
        "show all, thinking, tools, errors, or report",
        "presentation",
        argument_hint="all|thinking|tools|errors|report",
    ),
    _command("/find", "search the visible transcript", "presentation", argument_hint="<text>"),
    _command("/report", "jump to the final report", "presentation"),
    _command("/copy", "copy the final report", "presentation"),
    _command(
        "/attach",
        "attach a workspace file or directory",
        "session_action",
        "attach_paths",
        argument_hint="<path> [path ...]",
        argument_required=True,
    ),
    _command(
        "/attachments",
        "list files attached to this session",
        "session_action",
        "list_attachments",
    ),
    _command(
        "/detach",
        "remove a session attachment",
        "session_action",
        "detach_attachment",
        argument_hint="<path>",
        argument_required=True,
    ),
    _command("/paste", "attach files or an image from the macOS clipboard", "presentation"),
    _command("/theme", "switch visual theme", "presentation", argument_hint="<theme>"),
    _command(
        "/workflow",
        "select React or Agent Team workflow",
        "session_action",
        "switch_workflow",
        argument_hint="<workflow>",
    ),
    _command("/exit", "leave apodex", "presentation", aliases=("/quit",)),
)

_BY_TOKEN: dict[str, CommandSpec] = {}
for _spec in COMMANDS:
    _BY_TOKEN[_spec.name.lower()] = _spec
    for _alias in _spec.aliases:
        _BY_TOKEN[_alias.lower()] = _spec

_SHORTCUTS: tuple[dict[str, Any], ...] = (
    {"key": "f1", "action": "help", "browser": True},
    {"key": "f2", "action": "settings", "browser": True},
    {"key": "ctrl+p", "action": "command_palette", "browser": True},
    {"key": "meta+k", "action": "command_palette", "browser": True},
    {"key": "ctrl+b", "action": "toggle_sidebar", "browser": True},
    {"key": "ctrl+o", "action": "toggle_files", "browser": True},
    {"key": "alt+j", "action": "review_next", "browser": True},
    {"key": "alt+k", "action": "review_previous", "browser": True},
    {"key": "alt+enter", "action": "review_toggle", "browser": True},
    {"key": "ctrl+g", "action": "jump_report", "browser": True},
    {"key": "ctrl+y", "action": "copy_report", "browser": True},
    {"key": "ctrl+.", "action": "interrupt", "browser": True},
)


def get_command(token: str) -> CommandSpec | None:
    return _BY_TOKEN.get(token.strip().lower())


def command_palette_rows() -> tuple[tuple[str, str], ...]:
    return tuple((spec.name, spec.description) for spec in COMMANDS)


def commands_with_arguments() -> frozenset[str]:
    return frozenset(spec.name for spec in COMMANDS if spec.argument_hint)


def capabilities_payload() -> dict[str, Any]:
    return {
        "commands": [
            {
                "name": spec.name,
                "aliases": list(spec.aliases),
                "description": spec.description,
                "argument_hint": spec.argument_hint,
                "argument_required": spec.argument_required,
                "available_when_busy": spec.available_when_busy,
                "kind": spec.kind,
                "action": spec.action,
            }
            for spec in COMMANDS
        ],
        "shortcuts": [dict(item) for item in _SHORTCUTS],
    }
