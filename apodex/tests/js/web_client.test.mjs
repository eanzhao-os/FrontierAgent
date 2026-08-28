import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { loadPureClient } from "./extract.mjs";

describe("snapshot reducer", () => {
  it("records sequence and ignores duplicate ids", () => {
    const { applySnapshot, applyEvent, initialSessionState } = loadPureClient();
    let state = applySnapshot(initialSessionState(), {
      sequence: 10,
      revision: 2,
      session: { id: "s1", name: "", mode: "react", model: "m", cwd: "/tmp" },
      presentation: { phase: "idle", elapsed_seconds: null, tool_count: 0, queued: 0 },
      stream: { thinking: "", content: "" },
      transcript: { blocks: [], has_older: false, before: null },
      plan: { items: [], summary: "" },
      activity: { records: [], subagents: [], totals: {} },
      pending_approval: null,
    });
    assert.equal(state.lastEventId, 10);
    state = applyEvent(state, { id: 10, type: "note", data: { text: "dup" }, timestamp: 0 });
    assert.equal(state.notes.length, 0);
    state = applyEvent(state, { id: 11, type: "note", data: { text: "hi" }, timestamp: 0 });
    assert.equal(state.notes[0], "hi");
    assert.equal(state.lastEventId, 11);
  });
});
