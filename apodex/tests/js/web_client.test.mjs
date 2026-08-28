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

describe("composer parser", () => {
  it("rejects slash while busy", () => {
    const { parseComposerInput } = loadPureClient();
    const commands = [{ name: "/clear", aliases: [], available_when_busy: false }];
    const result = parseComposerInput("/clear", commands, true);
    assert.equal(result.kind, "slash");
    assert.match(result.error, /interrupt first/i);
  });

  it("queues steer when busy and text is not a slash command", () => {
    const { parseComposerInput } = loadPureClient();
    const result = parseComposerInput("stop that", [], true);
    assert.equal(result.kind, "steer");
  });

  it("matches unique slash prefixes", () => {
    const { matchSlashCommands } = loadPureClient();
    const commands = [{ name: "/clear" }, { name: "/copy" }, { name: "/compact" }];
    const hits = matchSlashCommands("/cl", commands).map((c) => c.name);
    assert.equal(hits.join(","), "/clear");
  });
});

describe("at-mention", () => {
  it("does not complete at-mentions inside slash commands", () => {
    const { completeAtMention, isSlashCommand } = loadPureClient();
    assert.equal(isSlashCommand("/attach ./x", ["/attach"]), true);
  });

  it("quotes paths that contain spaces", () => {
    const { completeAtMention } = loadPureClient();
    const out = completeAtMention("my f", [{ path: "my file.txt" }], []);
    assert.equal(out.completion.includes("\"") || out.completion.includes("'"), true);
  });
});
