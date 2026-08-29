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

describe("transcript", () => {
  it("filters tools and searches collapsed titles", () => {
    const { filterTranscript } = loadPureClient();
    const blocks = [
      { id: "1", kind: "thinking", title: "Thoughts", body: "abc", collapsed: true },
      { id: "2", kind: "tool", title: "bash ls", body: "out" },
      { id: "3", kind: "error", title: "fail", body: "no" },
      { id: "4", kind: "report", title: "Final", body: "done" },
    ];
    assert.equal(filterTranscript(blocks, "tools", "").map((b) => b.id).join(), "2");
    assert.equal(filterTranscript(blocks, "search", "thou").map((b) => b.id).join(), "1");
  });

  it("jump to report clears tools filter", () => {
    const { withJumpToReport } = loadPureClient();
    const next = withJumpToReport({ filter: "tools", blocks: [{ id: "r", kind: "report" }] });
    assert.equal(next.filter, "all");
    assert.equal(next.reviewId, "r");
  });
});

describe("plan and activity", () => {
  it("projects react todos and team board", () => {
    const { applyEvent, initialSessionState } = loadPureClient();
    let state = initialSessionState();
    state = applyEvent(state, { id: 1, type: "todos", data: { items: [{ content: "a", status: "pending" }] }, timestamp: 0 });
    assert.equal(state.plan.items[0].content, "a");
    state = applyEvent(state, { id: 2, type: "plan", data: { items: [{ id: "t1", status: "open", owner: "w1" }] }, timestamp: 0 });
    assert.equal(state.plan.items[0].id, "t1");
  });

  it("settles running activity on interrupt", () => {
    const { applyEvent, initialSessionState } = loadPureClient();
    let state = initialSessionState();
    state = applyEvent(state, { id: 1, type: "activity_call", data: { call_id: "c1", name: "bash", args: {} }, timestamp: 0 });
    state = applyEvent(state, { id: 2, type: "presentation", data: { phase: "interrupted" }, timestamp: 0 });
    assert.equal(state.activity.records.find((r) => r.call_id === "c1").state, "interrupted");
  });
});



describe("theme presets", () => {
  it("applies a named preset's variables and dark flag", () => {
    const { applyThemePreset } = loadPureClient();
    const presets = {
      mono: { dark: true, vars: { "--page": "#000000", "--ink": "#ffffff" } },
      light: { dark: false, vars: {} },
    };
    const out = applyThemePreset("mono", presets);
    assert.equal(out.dark, true);
    assert.equal(out.vars["--ink"], "#ffffff");
    assert.equal(applyThemePreset("nope", presets), null);
  });
});

describe("keyboard and reconnect", () => {
  it("maps ctrl-dot to interrupt and not ctrl-c", () => {
    const { shortcutAction } = loadPureClient();
    assert.equal(shortcutAction({ key: ".", ctrlKey: true, metaKey: false }), "interrupt");
    assert.equal(shortcutAction({ key: "c", ctrlKey: true, hasSelection: true }), "copy");
    assert.equal(shortcutAction({ key: "c", ctrlKey: true, hasSelection: false }), null);
    assert.equal(shortcutAction({ key: "p", ctrlKey: true }), "command_palette");
    assert.equal(shortcutAction({ key: "k", metaKey: true }), "command_palette");
  });

  it("reconnect with gap requests snapshot", () => {
    const { reconnectPlan } = loadPureClient();
    assert.equal(reconnectPlan({ lastEventId: 1, snapshotRequired: true }).mode, "snapshot");
    assert.equal(reconnectPlan({ lastEventId: 11, snapshotRequired: false }).mode, "replay");
  });
});

describe("snapshot rehydration", () => {
  it("rehydrates structured blocks into card-ready messages", () => {
    const { blocksToMessages } = loadPureClient();
    const msgs = blocksToMessages([
      { id: "b0", kind: "user", content: "go" },
      { id: "b1", kind: "tool", name: "add_task", call_id: "c1", content: "Added ['t1']" },
      { id: "b2", kind: "tool", name: "update_task", call_id: "c2", content: "Updated ['t1']" },
      { id: "b3", kind: "text", content: "done" },
    ]);
    assert.equal(msgs.length, 2);
    assert.equal(msgs[0].role, "user");
    assert.equal(msgs[0].content, "go");
    assert.equal(msgs[1].role, "assistant");
    assert.equal(msgs[1].toolCalls.length, 2);
    assert.equal(msgs[1].toolCalls[0].name, "add_task");
    assert.equal(msgs[1].toolCalls[0].result, "Added ['t1']");
    assert.equal(msgs[1].toolCalls[0].status, "completed");
    assert.equal(msgs[1].content, "done");
  });

  it("keeps bare text messages and tolerates legacy flat blocks", () => {
    const { blocksToMessages } = loadPureClient();
    const msgs = blocksToMessages([
      { id: "b0", kind: "text", content: "hello" },
      { id: "b1", role: "user", content: "legacy" },
      { id: "b2", role: "assistant", content: "legacy reply" },
    ]);
    assert.equal(msgs[1].role, "user");
    assert.equal(msgs[2].role, "assistant");
    assert.equal(msgs[2].content, "legacy reply");
    assert.ok(!msgs[2].toolCalls || msgs[2].toolCalls.length === 0);
  });
});

describe("session title formatting", () => {
  it("formats Agent Team and ReAct sessions correctly", () => {
    const { formatSessionTitle } = loadPureClient();
    assert.equal(
      formatSessionTitle({ session_id: "20260829-153000+0800-agent_team-abcd", mode: "agent_team" }),
      "Agent Team · abcd"
    );
    assert.equal(
      formatSessionTitle({ session_id: "20260829-153000+0800-react-1234", mode: "react" }),
      "ReAct · 1234"
    );
    assert.equal(
      formatSessionTitle({ session_id: "20260829-153000+0800-agent_team-abcd", name: "Custom Project", mode: "agent_team" }),
      "Custom Project"
    );
  });
});

describe("snapshot rehydration richness", () => {
  it("carries args, duration, error flag and thinking into the message", () => {
    const { blocksToMessages } = loadPureClient();
    const msgs = blocksToMessages([
      { id: "b0", kind: "user", content: "go" },
      { id: "b1", kind: "thinking", content: "plan first" },
      {
        id: "b2", kind: "tool", name: "add_task", call_id: "c1",
        content: "Added ['t1']", args: { tasks: [{ description: "read bazi db" }] },
        duration_ms: 12, is_error: false,
      },
      { id: "b3", kind: "text", content: "done" },
    ]);
    assert.equal(msgs[1].thinking, "plan first");
    const tc = msgs[1].toolCalls[0];
    assert.deepEqual(tc.args, { tasks: [{ description: "read bazi db" }] });
    assert.equal(tc.duration_ms, 12);
    assert.equal(tc.is_error, false);
  });
});
