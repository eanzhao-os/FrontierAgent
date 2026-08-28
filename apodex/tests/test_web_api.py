from __future__ import annotations


def test_steer_enqueues_on_inbox(web_manager):
    from apodex.render import Renderer
    from apodex.steer import SteerInbox

    web_manager.is_running = True
    inbox = SteerInbox(Renderer(theme="mono"))
    web_manager.session._inbox = inbox
    assert web_manager.steer("turn left") is True
    assert inbox.queue == ["turn left"]


def test_steer_without_running_task_is_ignored(web_manager):
    assert web_manager.steer("nope") is False


def test_steer_route_returns_ok_when_running(web_client, web_manager):
    from apodex.render import Renderer
    from apodex.steer import SteerInbox

    web_manager.is_running = True
    web_manager.session._inbox = SteerInbox(Renderer(theme="mono"))
    res = web_client.post("/api/steer", json={"instruction": "go"})
    assert res.status_code == 200
    assert res.json()["injected"] is True


def test_revert_uses_revert_all(web_client, web_manager, tmp_path):
    target = tmp_path / "a.txt"
    target.write_text("old\n")
    web_manager.session.journal.record_before(str(target))
    target.write_text("new\n")
    res = web_client.post("/api/revert")
    assert res.status_code == 200
    body = res.json()
    assert body["reverted"] == ["a.txt"]
    assert body["observed_only"] == []
    assert target.read_text() == "old\n"


def test_new_session_persists_previous(web_client, web_manager):
    old = web_manager.session.session_id
    web_manager.session.history = [{"role": "user", "content": "keep me"}]
    res = web_client.post("/api/sessions/new", json={"mode": "react"})
    assert res.status_code == 200
    assert web_manager.session.session_id != old


def test_busy_clear_returns_409(web_client, web_manager):
    web_manager.is_running = True
    res = web_client.post("/api/clear")
    assert res.status_code == 409
    assert res.json()["code"] == "busy"
    assert web_manager.session is not None


def test_pyproject_declares_web_runtime_deps():
    text = open("pyproject.toml", encoding="utf-8").read()
    for dep in ("fastapi", "uvicorn", "sse-starlette", "python-multipart"):
        assert dep in text
