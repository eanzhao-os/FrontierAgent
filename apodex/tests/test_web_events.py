"""EventBroadcaster sequence IDs and snapshot-first subscribe."""

from __future__ import annotations

import asyncio

from apodex.web_observer import EventBroadcaster


async def test_emit_assigns_monotonic_ids():
    bus = EventBroadcaster(max_history=3)
    a = await bus.emit("note", {"text": "a"})
    b = await bus.emit("note", {"text": "b"})
    assert a.sequence == 1 and b.sequence == 2
    assert bus.sequence == 2


async def test_replay_after_returns_later_events():
    bus = EventBroadcaster(max_history=10)
    await bus.emit("note", {"text": "a"})
    await bus.emit("note", {"text": "b"})
    later = bus.replay_after(1)
    assert [e.data["text"] for e in later] == ["b"]


async def test_replay_after_gap_returns_none():
    bus = EventBroadcaster(max_history=2)
    await bus.emit("note", {"text": "a"})
    await bus.emit("note", {"text": "b"})
    await bus.emit("note", {"text": "c"})
    assert bus.replay_after(1) is None


async def test_fresh_subscribe_does_not_replay_history():
    bus = EventBroadcaster()
    await bus.emit("note", {"text": "old"})
    queue = await bus.subscribe(None)
    assert queue.empty()
    await bus.emit("note", {"text": "live"})
    event = await asyncio.wait_for(queue.get(), timeout=1)
    assert event.data["text"] == "live"


def test_sse_frame_includes_id():
    event = asyncio.run(EventBroadcaster().emit("note", {"text": "a"}))
    frame = event.to_sse()
    assert frame.startswith("id: 1\n")
    assert '"type": "note"' in frame or '"type":"note"' in frame
