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


class _RichSession:
    """Duck-typed session: display_history wire messages + workflow turns."""

    def __init__(self):
        self.display_history = [
            {"role": "user", "content": "go"},
            {"role": "assistant", "content": ""},
            {"role": "tool", "content": "Added ['t1']", "tool_call_id": "c1"},
            {"role": "tool", "content": "Updated ['t1']", "tool_call_id": "c2"},
            {"role": "assistant", "content": "done"},
        ]
        self.workflow_turns = [
            {
                "messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {"id": "c1", "function": {"name": "add_task"}},
                            {"id": "c2", "function": {"name": "update_task"}},
                        ],
                    },
                ]
            }
        ]


def test_transcript_page_emits_kinds_and_tool_metadata():
    from apodex.session_snapshot import transcript_page

    page = transcript_page(_RichSession())
    kinds = [block["kind"] for block in page["blocks"]]
    assert kinds == ["user", "tool", "tool", "text"]
    tools = [block for block in page["blocks"] if block["kind"] == "tool"]
    assert tools[0]["call_id"] == "c1"
    assert tools[0]["name"] == "add_task"
    assert tools[1]["name"] == "update_task"
    assert page["blocks"][-1]["content"] == "done"


def test_transcript_page_survives_sessions_without_turns():
    from apodex.session_snapshot import transcript_page

    session = _RichSession()
    session.workflow_turns = []
    page = transcript_page(session)
    tools = [block for block in page["blocks"] if block["kind"] == "tool"]
    assert tools and tools[0]["name"] == ""  # no name mapping → blank, not a crash
