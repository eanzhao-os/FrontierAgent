from __future__ import annotations

from apodex.commands import COMMANDS, capabilities_payload, command_palette_rows, get_command

REQUIRED = {
    "/help", "/mode", "/workflow", "/model", "/settings", "/cwd", "/clear",
    "/new", "/fork", "/sessions", "/rename", "/plan", "/revert", "/compact",
    "/cost", "/context", "/config", "/init", "/resume", "/log", "/auto",
    "/bypass", "/autome", "/verbose", "/filter", "/find", "/report", "/copy",
    "/attach", "/attachments", "/detach", "/paste", "/theme", "/exit",
}


def test_registry_lists_every_canonical_command():
    assert {spec.name for spec in COMMANDS} == REQUIRED


def test_aliases_resolve_to_canonical_specs():
    assert get_command("/quit").name == "/exit"
    assert get_command("/menu").name == "/settings"
    assert get_command("/auto-for-me").name == "/autome"
    assert get_command("/QUIT").name == "/exit"


def test_argument_commands_match_tui():
    with_args = {
        "/mode", "/model", "/cwd", "/theme", "/workflow", "/filter", "/find",
        "/attach", "/detach", "/rename",
    }
    for spec in COMMANDS:
        if spec.name in with_args:
            assert spec.argument_hint
        if spec.name in {"/rename", "/attach", "/detach"}:
            assert spec.argument_required is True


def test_busy_slash_commands_are_unavailable():
    assert all(spec.available_when_busy is False for spec in COMMANDS)


def test_palette_rows_cover_canonical_names():
    assert {name for name, _desc in command_palette_rows()} == REQUIRED


def test_capabilities_payload_includes_aliases_and_shortcuts():
    payload = capabilities_payload()
    by_name = {item["name"]: item for item in payload["commands"]}
    assert "/quit" in by_name["/exit"]["aliases"]
    keys = {item["key"] for item in payload["shortcuts"]}
    assert {"f1", "f2", "ctrl+p", "meta+k", "ctrl+."} <= keys


def test_capabilities_http_matches_registry(web_client):
    names = {item["name"] for item in web_client.get("/api/capabilities").json()["commands"]}
    assert names == {spec.name for spec in COMMANDS}


def test_tui_palette_matches_registry():
    from apodex.commands import COMMANDS, command_palette_rows
    from apodex.tui.app import _COMMANDS

    assert tuple(command_palette_rows()) == _COMMANDS
    assert {spec.name for spec in COMMANDS} == {name for name, _ in _COMMANDS}
