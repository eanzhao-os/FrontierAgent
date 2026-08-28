import asyncio

from apodex.web_observer import EventBroadcaster, WebRenderer


async def _collect(renderer, fn):
    bus = renderer.broadcaster
    q = await bus.subscribe(None)
    fn()
    await asyncio.sleep(0)
    events = []
    while not q.empty():
        events.append(await q.get())
    return events


def test_renderer_emits_todos_not_stdout(capsys):
    bus = EventBroadcaster()
    r = WebRenderer(bus)
    events = asyncio.run(_collect(r, lambda: r.todos([{"content": "a", "status": "pending"}])))
    assert any(e.event_type == "todos" for e in events)
    assert capsys.readouterr().out == ""


def test_renderer_emits_activity_plan_queued_llm_failure(capsys):
    bus = EventBroadcaster()
    r = WebRenderer(bus)

    def go():
        r.activity_call("bash", {"command": "ls"}, call_id="c1")
        r.activity_result("bash", "ok", call_id="c1", is_error=False, ms=3)
        r.plan_review("do x")
        r.queued("steer later")
        r.llm_failure("no key", configuration_error=True)

    events = asyncio.run(_collect(r, go))
    types = {e.event_type for e in events}
    assert {"activity_call", "activity_result", "plan_review", "queued", "llm_failure"} <= types
    assert capsys.readouterr().out == ""


def test_incomplete_is_not_a_final_success():
    bus = EventBroadcaster()
    r = WebRenderer(bus)
    events = asyncio.run(_collect(r, lambda: r.incomplete("stopped", turns=1, tool_calls=0, stopped_by="interrupt")))
    finals = [e for e in events if e.event_type == "final_answer"]
    assert finals and finals[-1].data["status"] == "incomplete"
