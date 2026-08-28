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


def test_capabilities_match_registry(web_client):
    from apodex.commands import COMMANDS

    names = {item["name"] for item in web_client.get("/api/capabilities").json()["commands"]}
    assert names == {spec.name for spec in COMMANDS}


def test_state_returns_snapshot_shape(web_client):
    snap = web_client.get("/api/state").json()
    assert {"revision", "sequence", "session", "transcript", "pending_approval"} <= set(snap)


def test_actions_revision_conflict(web_client, web_manager):
    web_manager.revision = 4
    res = web_client.post(
        "/api/actions",
        json={"action": "clear_context", "arguments": {}, "expected_revision": 1},
    )
    assert res.status_code == 409
    assert res.json()["code"] == "revision_conflict"


def test_unknown_action_is_validation(web_client):
    res = web_client.post("/api/actions", json={"action": "not_a_thing", "arguments": {}})
    assert res.status_code == 400
    assert res.json()["code"] == "validation"


def test_snapshot_or_replay_reports_gap(web_manager):
    from apodex.web_server import snapshot_or_replay

    bus = web_manager.broadcaster
    bus._history.clear()
    bus._sequence = 5
    assert snapshot_or_replay(bus, last_id=1) == "snapshot_required"


def test_resolve_dangerous_rejects_without_yes():
    import asyncio

    from apodex.web_observer import EventBroadcaster, WebApprover

    bus = EventBroadcaster()
    approver = WebApprover(bus)

    async def body():
        task = asyncio.create_task(approver.confirm("bash", "rm", "destroy", dangerous="delete"))
        await asyncio.sleep(0)
        appr_id = next(iter(approver._pending))
        assert approver.resolve(appr_id, decision="approve", confirmation="") is False
        ok = approver.resolve(appr_id, decision="approve", confirmation="yes")
        assert ok is True
        decision = await task
        assert decision.approved is True

    asyncio.run(body())


def test_redirect_sets_feedback_and_rejects():
    import asyncio

    from apodex.web_observer import EventBroadcaster, WebApprover

    bus = EventBroadcaster()
    approver = WebApprover(bus)

    async def body():
        task = asyncio.create_task(approver.confirm("bash", "ls", "run"))
        await asyncio.sleep(0)
        appr_id = next(iter(approver._pending))
        assert approver.resolve(appr_id, decision="redirect", feedback="use python")
        decision = await task
        assert decision.approved is False
        assert decision.feedback == "use python"

    asyncio.run(body())


def test_always_allow_sets_remember():
    import asyncio

    from apodex.web_observer import EventBroadcaster, WebApprover

    bus = EventBroadcaster()
    approver = WebApprover(bus)

    async def body():
        task = asyncio.create_task(approver.confirm("bash", "ls", "run"))
        await asyncio.sleep(0)
        appr_id = next(iter(approver._pending))
        assert approver.resolve(appr_id, decision="always_allow")
        decision = await task
        assert decision.approved is True and decision.remember is True

    asyncio.run(body())


def test_allow_session_flips_auto_approve():
    import asyncio

    from apodex.web_observer import EventBroadcaster, WebApprover

    bus = EventBroadcaster()
    approver = WebApprover(bus)

    async def body():
        task = asyncio.create_task(approver.confirm("bash", "ls", "run"))
        await asyncio.sleep(0)
        appr_id = next(iter(approver._pending))
        assert approver.resolve(appr_id, decision="allow_session")
        decision = await task
        assert decision.approved is True
        assert approver.auto_approve is True

    asyncio.run(body())


def test_auto_for_me_flips_flag():
    import asyncio

    from apodex.web_observer import EventBroadcaster, WebApprover

    bus = EventBroadcaster()
    approver = WebApprover(bus)

    async def body():
        task = asyncio.create_task(approver.confirm("bash", "ls", "run"))
        await asyncio.sleep(0)
        appr_id = next(iter(approver._pending))
        assert approver.resolve(appr_id, decision="auto_for_me")
        decision = await task
        assert decision.approved is True
        assert approver.auto_for_me is True

    asyncio.run(body())


def test_approve_route_dangerous_confirmation(web_client, web_manager):
    import asyncio

    async def body():
        task = asyncio.create_task(
            web_manager.approver.confirm("bash", "rm", "destroy", dangerous="delete")
        )
        await asyncio.sleep(0)
        appr_id = next(iter(web_manager.approver._pending))
        denied = web_client.post(
            "/api/approve",
            json={"id": appr_id, "decision": "approve", "confirmation": ""},
        )
        assert denied.status_code == 400
        assert denied.json()["code"] == "dangerous_confirmation"
        ok = web_client.post(
            "/api/approve",
            json={"id": appr_id, "decision": "approve", "confirmation": "yes"},
        )
        assert ok.status_code == 200
        decision = await task
        assert decision.approved is True

    asyncio.run(body())


def test_file_route_rejects_home_escape(web_client, web_manager, tmp_path):
    secret = tmp_path.parent / f"{tmp_path.name}-home-secret.txt"
    secret.write_text("nope")
    res = web_client.get("/api/file", params={"path": str(secret)})
    assert res.status_code in (403, 404)


def test_symlink_escape_is_rejected(web_client, web_manager, tmp_path):
    sibling = tmp_path.parent / f"{tmp_path.name}-secret.txt"
    sibling.write_text("secret")
    sneak = tmp_path / "sneak.txt"
    sneak.symlink_to(sibling)
    res = web_client.get("/api/file", params={"path": str(sneak)})
    assert res.status_code in (403, 404)


def test_allowed_file_path_accepts_workspace(tmp_path):
    from apodex.web_paths import allowed_file_path

    target = tmp_path / "in.txt"
    target.write_text("ok")
    assert allowed_file_path(
        str(target),
        cwd=str(tmp_path),
        session_id="s",
        run_roots=[],
        inputs_dir=None,
        outputs_dir=None,
    ) == target.resolve()


def test_interrupt_resolves_pending_approval(web_manager):
    import asyncio

    async def body():
        task = asyncio.create_task(web_manager.approver.confirm("bash", "ls", "run"))
        await asyncio.sleep(0)
        web_manager.settle_interrupt()
        decision = await asyncio.wait_for(task, timeout=1)
        assert decision.approved is False

    asyncio.run(body())
