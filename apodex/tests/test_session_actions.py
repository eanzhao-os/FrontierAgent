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
