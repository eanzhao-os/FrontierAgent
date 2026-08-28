from apodex.config import ModelConfig
from apodex.render import Renderer
from apodex.session import TerminalSession
from apodex.session_snapshot import build_session_snapshot
from frontier_agent.core.messages import user_msg


def test_snapshot_has_required_keys_and_redacts_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    session = TerminalSession(
        cfg=ModelConfig(model="fake", api_key="sk-secret-value", base_url="http://127.0.0.1:8000/v1"),
        cwd=str(tmp_path), renderer=Renderer(theme="mono"),
        auto_approve=True, max_turns=5, interactive=False, mode="coding",
    )
    session.history = [user_msg("hi")]
    snap = build_session_snapshot(
        session, revision=3, sequence=9, runtime_status="ready", pending_approval=None,
    )
    for key in (
        "revision", "sequence", "session", "runtime", "presentation", "stream",
        "transcript", "plan", "activity", "attachments", "artifacts", "changes",
        "pending_approval",
    ):
        assert key in snap
    assert snap["revision"] == 3
    assert snap["sequence"] == 9
    assert snap["session"]["id"] == session.session_id
    blob = str(snap)
    assert "sk-secret-value" not in blob
    assert "http://127.0.0.1:8000/v1" not in blob
