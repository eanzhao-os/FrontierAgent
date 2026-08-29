import asyncio

from apodex.config import ModelConfig
from apodex.render import Renderer
from apodex.session import TerminalSession, load_session_state
from apodex.session_actions import SessionActions
from apodex.todo import TodoItem, get_todos, set_todos
from frontier_agent.core.messages import assistant_msg, user_msg


def _session(tmp_path, monkeypatch, mode="coding"):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("APODEX_INPUT_STAGING_DIR", raising=False)
    monkeypatch.delenv("FRONTIER_AGENT_INPUTS_DIR", raising=False)
    return TerminalSession(
        cfg=ModelConfig(model="fake", api_key="x", base_url=None),
        cwd=str(tmp_path),
        renderer=Renderer(theme="mono"),
        auto_approve=True,
        max_turns=5,
        interactive=False,
        mode=mode,
    )


def test_new_session_saves_checkpoint_and_resets_context(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    session.history = [user_msg("old"), assistant_msg("ans")]
    session.plan_state.active = True
    old_id = session.session_id
    result = SessionActions(session).new_session(fork=False)
    assert result.ok
    assert result.data["previous"] == old_id
    assert result.data["session_id"] != old_id
    assert load_session_state(old_id)["history"] == [user_msg("old"), assistant_msg("ans")]
    assert session.history == []
    assert session.plan_state.active is True


def test_fork_retains_history_with_new_id(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    session.history = [user_msg("keep")]
    old_id = session.session_id
    result = SessionActions(session).new_session(fork=True)
    assert result.ok
    assert session.session_id != old_id
    assert session.history == [user_msg("keep")]


def test_clear_does_not_toggle_plan_mode(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    session.plan_state.active = True
    session.user_settings.plan_mode = True
    session.history = [user_msg("x")]
    set_todos([TodoItem("step", "pending")])
    result = SessionActions(session).clear_context()
    assert result.ok
    assert session.history == []
    assert get_todos() == []
    assert session.plan_state.active is True
    assert session.user_settings.plan_mode is True


def test_revert_returns_observed_only_separately(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    target = tmp_path / "built.js"
    target.write_text("before\n")
    before = session.journal.begin_tree_scan([str(tmp_path)])
    target.write_text("after\n")
    session.journal.finish_tree_scan([str(tmp_path)], before)
    result = SessionActions(session).revert_changes()
    assert result.ok
    assert result.data["reverted"] == []
    assert "built.js" in result.data["observed_only"]
    assert "nothing to revert (no attributed edits)" in result.message


def test_rename_validates_blank_and_length(tmp_path, monkeypatch):
    actions = SessionActions(_session(tmp_path, monkeypatch))
    bad = actions.rename_session("   ")
    assert bad.ok is False and bad.code == "validation"
    ok = actions.rename_session("demo")
    assert ok.ok and ok.data["name"] == "demo"


def test_resume_missing_session_is_not_found(tmp_path, monkeypatch):
    result = SessionActions(_session(tmp_path, monkeypatch)).resume_session("no-such")
    assert result.ok is False and result.code == "not_found"


def test_switch_model_keeps_history(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    session.history = [{"role": "user", "content": "stay"}]
    session.models = ["fake", "other"]
    result = SessionActions(session).switch_model("other")
    assert result.ok
    assert session.cfg.model == "other"
    assert session.history == [{"role": "user", "content": "stay"}]


def test_switch_workflow_unknown_is_validation(tmp_path, monkeypatch):
    result = SessionActions(_session(tmp_path, monkeypatch)).switch_workflow("nope")
    assert result.ok is False and result.code == "validation"


def test_slash_adapter_uses_actions(tmp_path, monkeypatch):
    session = _session(tmp_path, monkeypatch)
    session.history = [{"role": "user", "content": "x"}]
    assert asyncio.run(session._slash("/clear")) is False
    assert session.history == []


def test_new_session_with_mode_switches_workflow(tmp_path, monkeypatch):

    session = _session(tmp_path, monkeypatch, mode="react")
    assert session.mode == "react"
    result = SessionActions(session).new_session(mode="agent_team")
    assert result.ok
    assert session.mode == "agent_team"
    assert "-agent_team-" in session.session_id


def test_archive_and_restore_session(tmp_path, monkeypatch):
    from apodex.session_state import list_saved_sessions

    session = _session(tmp_path, monkeypatch)
    session.history = [user_msg("hi")]
    sid = session.session_id
    actions = SessionActions(session)

    # Archive active session
    res_arch = actions.archive_session(sid, archived=True)
    assert res_arch.ok
    assert res_arch.data["archived"] is True
    assert session.archived is True
    saved = list_saved_sessions(workspace=tmp_path)
    current_meta = next(s for s in saved if s["session_id"] == sid)
    assert current_meta["archived"] is True

    # Restore active session
    res_res = actions.archive_session(sid, archived=False)
    assert res_res.ok
    assert res_res.data["archived"] is False
    assert session.archived is False
    saved = list_saved_sessions(workspace=tmp_path)
    current_meta = next(s for s in saved if s["session_id"] == sid)
    assert current_meta["archived"] is False


def test_pin_and_unpin_session(tmp_path, monkeypatch):
    from apodex.session_state import list_saved_sessions

    session = _session(tmp_path, monkeypatch)
    session.history = [user_msg("hi")]
    sid = session.session_id
    actions = SessionActions(session)

    res_pin = actions.pin_session(sid, pinned=True)
    assert res_pin.ok
    assert res_pin.data["pinned"] is True
    assert session.pinned is True
    saved = list_saved_sessions(workspace=tmp_path)
    current_meta = next(s for s in saved if s["session_id"] == sid)
    assert current_meta["pinned"] is True

    res_unpin = actions.pin_session(sid, pinned=False)
    assert res_unpin.ok
    assert res_unpin.data["pinned"] is False
    assert session.pinned is False
    saved = list_saved_sessions(workspace=tmp_path)
    current_meta = next(s for s in saved if s["session_id"] == sid)
    assert current_meta["pinned"] is False


def test_empty_session_is_not_persisted_or_listed(tmp_path, monkeypatch):
    from apodex.session_state import list_saved_sessions

    session = _session(tmp_path, monkeypatch)
    empty_id = session.session_id

    result = SessionActions(session).new_session(fork=False)
    assert result.ok
    assert result.data["previous"] == empty_id
    # No input ever sent → the empty shell was never persisted nor listed
    assert load_session_state(empty_id) is None
    ids = [s["session_id"] for s in list_saved_sessions(workspace=tmp_path)]
    assert empty_id not in ids


def test_delete_session(tmp_path, monkeypatch):
    from apodex.run_layout import run_dir

    session = _session(tmp_path, monkeypatch)
    sid = session.session_id
    r_dir = run_dir(sid, workspace=tmp_path)
    assert r_dir.is_dir()

    actions = SessionActions(session)
    res = actions.delete_session(sid)
    assert res.ok
    assert res.data["deleted"] is True
    assert res.data["active_reset"] is True
    # Previous run directory is removed
    assert not r_dir.exists()
    # A fresh session is started
    assert session.session_id != sid

