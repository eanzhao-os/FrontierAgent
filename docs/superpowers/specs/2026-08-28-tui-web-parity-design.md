# FrontierAgent TUI-to-Web Parity Design

**Date:** 2026-08-28
**Status:** Approved in conversation
**Scope:** Move every user-facing TUI capability into the personal Web UI through both native Web controls and the existing command/keyboard vocabulary.

## Summary

FrontierAgent's Web UI currently covers the central demo path—streaming output, basic tool cards, basic approval, sessions, workspaces, Agent Team board projection, and deliverables—but it is not behaviorally equivalent to the TUI. Several visible controls are also disconnected from the real runtime semantics: Web steering calls a nonexistent `SteerInbox.steer()` method, Web revert calls a nonexistent `WorkspaceJournal.revert()` method, and the Web renderer does not implement several callbacks that `TerminalObserver` relies on.

The selected design is a shared capability layer. TUI commands and Web REST actions will call the same structured session operations, and both surfaces will continue to use `TerminalObserver` as the only execution, safety, plan-mode, steering, and journal observer. The Web surface will retain its single-file, no-build React architecture and native-ai-ui semantic tokens.

The product decision is **dual-entry parity**:

- Every capability has a visible Web control where one is useful.
- The full slash-command vocabulary remains available in the composer and command palette.
- TUI shortcuts are preserved where browsers allow it, with visible controls and browser-safe aliases where they do not.
- Browser-native representations may improve the carrier without weakening the capability—for example, a PDF may use the browser viewer rather than terminal text extraction.

## Goals

1. Give the Web UI behavioral parity with all user-facing TUI interactions and session operations.
2. Remove duplicated TUI/Web session semantics so future commands cannot silently land on one surface only.
3. Preserve the safety contract: hard denials, read-before-edit, plan mode, dangerous confirmation, attributed revert, and secret-free configuration diagnostics.
4. Make reconnect, resume, fork, and long-running intervention recover the same semantic state the TUI preserves.
5. Keep the existing personal, local, BYOK operating model and the current single-file/no-build Web delivery model.
6. Add automated parity gates so a new TUI command or renderer callback cannot omit the Web UI unnoticed.

## Non-goals

- Converting the Web UI to Next.js, Vite, or another build pipeline.
- Adding multi-user authentication, cloud tenancy, or a remote hosted control plane.
- Rewriting the agent loop, ReAct workflow, Agent Team workflow, or tool protocol.
- Pixel-for-pixel reproduction of terminal widgets when a browser-native carrier is clearer.
- Letting `/exit` terminate the host Web server remotely.
- Broad refactors unrelated to TUI/Web parity.

## Current-State Findings

### Capabilities already present in some form

- Named-event SSE streaming for thinking, content, tool calls/results, approvals, usage, final answers, and sub-agent snapshots.
- ReAct and Agent Team execution through a shared `TerminalSession`.
- A three-column Web layout with workspace/session navigation, transcript, and a right workspace.
- Basic one-time approval/rejection and session auto-approval.
- Session creation/resume, workspace management, deliverable listing, Markdown preview, task-board projection, token totals, and change counts.

### Partial or incorrect behavior

- `/api/steer` calls `SteerInbox.steer()`, which does not exist; the real TUI queues with `enqueue()`.
- `/api/revert` calls `WorkspaceJournal.revert()`, which does not exist; TUI semantics use `revert_all()` and separately report observed-only paths.
- Web clear does not perform TUI clear semantics for usage, task-plan/todo content, file guard state, spill context, activity, or presentation state.
- Web new-session construction bypasses `TerminalSession.start_new_session()`, so save/fork/reset behavior diverges.
- `WebRenderer` omits `todos`, `activity_call`, `activity_result`, `plan_review`, `llm_failure`, `queued`, and presentation-state behavior. The inherited terminal renderer writes these to stdout rather than SSE.
- The imported `WebObserver` is unused; the runtime actually uses `TerminalObserver`. It duplicates risk and tool-flow logic and is already drifting.
- React todos, plan-review content, full Activity history, full session diff, queued-steer counts, configuration failures, and incomplete/error distinctions do not reach the Web UI reliably.
- Resume reconstructs only part of display history, can lose tool results/board/sub-agent state, and does not provide an atomic semantic snapshot.
- SSE reconnect replays the broadcaster history without event IDs or client de-duplication.
- The approval card lacks persistent allow, Auto for me, dangerous typed confirmation, and TUI's reject-versus-redirect semantics.
- The file endpoint currently allows any file below the user's home directory instead of the narrower workspace/run/input/output boundaries used by the product interaction.

### Missing interaction families

- Command palette, slash hints/completion, complete command handling, input history, and help.
- Attach/detach/list, clipboard image/file handling, drag-and-drop, and `@` attachment/workspace completion.
- Transcript filtering, search, review navigation, report jump/copy, and long-session render bounds.
- Model/workflow switching, fork, rename, compact, plan-mode toggle, config/log/context views, and unified settings.
- TUI-equivalent Activity, Files, and Diff panes, including all preview adapters.
- First-run secret-free setup guidance, curated theme presets, comprehensive shortcuts, responsive focus behavior, and explicit reconnect recovery.

## Architecture

### High-level flow

```text
TUI slash command / shortcut ─┐
                              ├─ CommandRegistry ── SessionActions ── TerminalSession
Web control / slash command ──┘                                         │
                                                                        ▼
                                                         TerminalObserver
                                                          ├─ TuiSink → Textual
                                                          └─ WebRenderer → EventBroadcaster
                                                                           │
                                                     named SSE increments ──┤
                                                     /api/state snapshot ───┘
                                                                           ▼
                                                                  Web state reducer
```

### CommandRegistry

Create a single structured registry for the full interactive command vocabulary. Each `CommandSpec` contains:

- canonical name and aliases;
- localized description key;
- argument syntax and whether an argument is required;
- availability while busy;
- execution kind: shared session action, Web/TUI presentation action, or task submission;
- optional keyboard shortcuts and browser-safe aliases.

The TUI command tuple and command palette derive from this registry. `/api/capabilities` exposes the same registry to the Web composer. A contract test compares the registry with both surfaces.

The canonical vocabulary is:

```text
/help /mode /workflow /model /settings /cwd /clear /new /fork /sessions
/rename /plan /revert /compact /cost /context /config /init /resume /log
/auto /bypass /autome /verbose /filter /find /report /copy /attach
/attachments /detach /paste /theme /exit
```

Existing aliases remain, including `/quit`, `/menu`, and `/auto-for-me`.

Commands such as `/filter`, `/find`, `/report`, `/copy`, `/help`, `/settings`, `/paste`, `/theme`, and `/exit` have presentation work on each surface. Their presence and argument rules still come from the shared registry, while each surface supplies the carrier-specific handler.

### SessionActions

Add a backend-neutral action service around `TerminalSession`. It exposes explicit synchronous or asynchronous operations and returns structured `ActionResult` values rather than renderer prose. Required actions include:

- `new_session(fork=False)` and `new_session(fork=True)`;
- `rename_session(name)` and `resume_session(id)`;
- `clear_context()` and `compact_context()`;
- `switch_workflow(name)`, `switch_model(name)`, and `change_cwd(path)`;
- `set_plan_mode(enabled)` and `set_verbose(enabled)`;
- `set_auto_approve(enabled)` and `set_auto_for_me(enabled)`;
- `revert_changes()`;
- `attach_paths(paths)`, `list_attachments()`, and `detach_attachment(name)`;
- safe runtime configuration, context/cost, trace-path, and session-list queries.

`TerminalSession._slash()` becomes a thin text adapter around these actions. Web endpoints call the same actions directly. An action owns all associated state updates and persistence; the TUI and Web adapters only render the returned result.

Important semantics remain unchanged:

- New saves the current checkpoint and starts an empty context with a new session ID.
- Fork saves the current checkpoint and starts a new session ID with retained model/display/workflow history.
- Workflow and cwd changes reset context as the TUI does.
- Model switching rebuilds the client without discarding the current conversation.
- Clear discards conversation, task-plan/todo content, usage context, file-guard state, and session-private overflow references while leaving repository state and the current Plan Mode preference unchanged.
- Revert restores only attributed file-tool changes and returns observed-only paths separately.
- Settings changes update `UserSettings` with the same persistence behavior as the TUI.

### One runtime observer

`TerminalObserver` remains the only observer responsible for:

- risk assessment and hard denials;
- read-before-edit;
- plan-mode mutation blocking and `exit_plan_mode` review;
- approval, rejection, redirect, persistent allow, and Auto for me;
- journal snapshots/tree scans;
- steering injection and late follow-up behavior;
- todo reminders and tool lifecycle.

Remove the unused `WebObserver` implementation after its event-specific functionality is represented in `WebRenderer`. Keeping two implementations would recreate the drift this project is fixing.

### WebRenderer and semantic presentation state

`WebRenderer` must implement every renderer callback reachable from `TerminalObserver` and `TaskRunnerMixin`; it must never fall back to terminal printing for a user-visible event.

It keeps a bounded semantic mirror equivalent to the TUI's presentation state:

- phase: idle, thinking, responding, running tool, awaiting approval, done, incomplete, interrupted, or error;
- start/end time, current tool, queued steer count, and session-total tool count;
- active stream content/thinking;
- the most recent 100 Activity records plus session-total counters;
- normalized ReAct todo and Agent Team task-board items;
- current sub-agent snapshots and their expandable events;
- final report status and text;
- pending approval, attachments, and current change summary.

The Web projection reuses backend-neutral coercion for `add_task` and `update_task`; the browser no longer parses human-readable task-board result text with a regex. The same projection helper is used by the TUI.

### SessionSnapshot

`GET /api/state` returns an atomic semantic snapshot with a monotonically increasing `revision` and current event `sequence`:

```json
{
  "revision": 12,
  "sequence": 481,
  "session": {"id": "...", "name": "...", "mode": "react", "model": "...", "cwd": "..."},
  "runtime": {"status": "ready", "config": {}, "usage": {}},
  "presentation": {"phase": "idle", "elapsed_seconds": null, "tool_count": 0, "queued": 0},
  "stream": {"thinking": "", "content": ""},
  "transcript": {"blocks": [], "has_older": false, "before": null},
  "plan": {"items": [], "summary": ""},
  "activity": {"records": [], "subagents": [], "totals": {}},
  "attachments": [],
  "artifacts": [],
  "changes": {"stats": [], "diff": "", "observed_only": []},
  "pending_approval": null
}
```

The snapshot uses normalized presentation blocks, not raw OpenAI wire messages. Raw persisted history remains the model/session source of truth. Long transcripts expose the latest 300 rendered blocks and a cursor endpoint for older blocks, matching the TUI's bounded rendering without discarding history.

### Event ordering and reconnect

SSE remains a named-event stream. Event JSON retains the existing shape:

```json
{"type": "tool_call", "data": {}, "timestamp": 0.0}
```

Each SSE frame additionally carries an SSE `id:` generated by `EventBroadcaster`; no incompatible JSON wrapper is introduced. Existing event names remain valid during the migration. New semantic events are additive, including presentation, plan, activity, attachments, changes, and session-state updates.

On first load the client obtains `/api/state`, records its sequence, then applies later events. On a reconnect it sends the last event ID; the server either replays later retained events or instructs the client to refresh the snapshot if the gap is outside the bounded event history. Session changes invalidate old-session increments before new ones are delivered. An expired or resolved approval cannot reappear as pending.

## Web API

Existing routes remain as compatibility wrappers where practical. They delegate to the shared action layer instead of containing independent state mutations.

### New or expanded read routes

- `GET /api/capabilities` — commands, aliases, argument shapes, shortcuts, modes, models, themes, and preview types.
- `GET /api/state` — atomic current-session snapshot.
- `GET /api/transcript?before=<cursor>` — older normalized transcript blocks.
- `GET /api/attachments` — current session attachment metadata.
- `GET /api/files/search?q=<query>` — bounded attachment/workspace completion candidates.
- `GET /api/preview?path=<path>` — structured preview metadata/content.
- `GET /api/file/raw?path=<path>` — safe inline/download response for browser-native viewers.
- `GET /api/diff` — complete session-baseline diff, per-file stats, revertable flags, and observed-only paths.

### New or expanded mutation routes

- `POST /api/actions` — a Pydantic discriminated action request with `action`, typed arguments, and optional `expected_revision`.
- `POST /api/attachments/path` — copy one or more host paths through `AttachmentManager`.
- `POST /api/attachments/upload` — stream browser-selected files into session attachment staging.
- `DELETE /api/attachments/{name}` — remove only the session copy.
- `POST /api/approve` — accept a structured decision: approve once, reject, redirect, Auto for me, allow session, or always allow this command. Dangerous approval requires `confirmation: "yes"` and is checked server-side.

`/api/run`, `/api/steer`, `/api/interrupt`, `/api/revert`, `/api/clear`, session routes, workspace routes, and mode routes remain but use shared actions. Unsupported busy-state mutations return HTTP 409 with a stable code and readable message.

## Web Interaction Design

### Layout

Wide screens use three columns:

1. **Left:** workspaces and sessions, including new ReAct/Team session, resume, inspect, fork, rename, and workspace management.
2. **Center:** status/top bar, transcript, approvals, and a single composer for tasks and steering.
3. **Right:** TUI-aligned `Plan`, `Activity`, `Files`, and conditional `Diff` tabs.

The current Web `Team` tab moves into Activity as the `SUB-AGENTS` group. The current `Status` tab moves into the top status strip and the F2 diagnostics/settings view. This keeps the right workspace aligned with the TUI and avoids duplicating the same information in two structures.

At medium widths, either side becomes a drawer. At narrow widths the center remains primary and the four work tabs open as a bottom sheet. Focus returns to the composer after dialogs and list previews close.

### Composer, command palette, and completion

- Idle Enter submits a task; busy Enter queues a steer.
- Busy slash commands are rejected with the same “interrupt first” guidance as the TUI.
- Slash input shows matching commands, description, argument hint, and keyboard selection.
- `Ctrl-P` and `Cmd/Ctrl-K` open the complete command palette.
- Up/down history preserves a draft and does not override normal multiline cursor movement.
- `@` searches explicit attachments first and workspace files second, uses case-insensitive shared-prefix completion, quotes paths with spaces, and is disabled inside real slash commands.
- Drag/drop, file selection, clipboard files/images, host-path attach, and large/multiline text preserve their original bytes/text. Large text may display as a compact editable marker while retaining the full submitted value.
- Attachment chips show relative name and size and support detach without touching the source.

### Keyboard behavior

Preserve these interactions where the browser allows them:

- F1 help, F2 settings;
- Ctrl-P/Cmd-Ctrl-K command palette;
- Ctrl-B panel visibility;
- Ctrl-O Files/Plan toggle;
- Alt-J/Alt-K transcript block navigation;
- Alt-Enter expand/collapse;
- Ctrl-G jump to report;
- Ctrl-Y copy report;
- Up/down prompt history;
- Tab command or `@` completion.

Browsers own Ctrl-Tab, and Ctrl-C must retain normal copy. Sidebar tabs therefore also expose visible controls and Alt-Left/Alt-Right aliases. Interrupt always has a visible Stop control and `Ctrl-.`; Ctrl-C may interrupt only when there is no text selection and no editable copy target.

### Transcript

The transcript preserves chronological user messages, thinking, narration, tool calls/results, approvals, redirects/rejections, run-level notes/errors, and final/incomplete reports.

- Long thinking auto-collapses at the same 900-character/10-line threshold as the TUI and can be reopened.
- Tool/process blocks use progressive disclosure and retain reviewable full output within the same bounded display policy.
- Filters support all, thinking, tools, errors, and report.
- Search includes plain blocks and collapsed titles and reports the result count.
- Review navigation highlights the current block without stealing composer focus.
- Jump-to-report clears an incompatible filter, and copy uses the latest final report text even when no streamed prose block exists.
- Auto-follow pauses when the user scrolls away, offers a “jump to latest” control, and resumes only by an explicit return to the tail.
- The rendered block window is bounded to 300 while older history remains loadable.

### Plan

- ReAct `todo_write` renders pending, in-progress, and completed items.
- Agent Team `add_task`/`update_task` renders open, in-progress, resolved, and cancelled items with owners.
- The title shows completed/total progress.
- A new task clears the prior board; task completion leaves the current board visible until the next task.

### Activity

- The most recent 100 tool records stay selectable while session-total counters remain cumulative.
- States are running, queued, success, failed, skipped, and interrupted.
- Detail shows state, duration, call ID, arguments, result summary, and available full result/preview.
- Agent Team shows `SUB-AGENTS` and `COORDINATOR` groups with live/failed counts.
- Each worker has a stable identity color and inferred specialty and can expand thinking, messages, tool calls/results, and errors.
- Interrupt settles every active row rather than leaving permanent spinners.

### Approval and plan review

The default focused decision is No. Ordinary approval offers:

- approve once;
- reject and stop;
- redirect with feedback and continue;
- Auto for me for Bash/internal operations in trusted environments;
- allow ordinary approvals for the current session;
- persistently allow this command class.

Dangerous actions require typing the full word `yes`; a one-key or forged boolean request is rejected server-side. Approval shows target, reason, danger, command/diff preview, size/count summary, and scroll controls. Plan review shows the submitted plan body and uses approve or revise-with-feedback semantics while plan mode remains active until approval.

### Files and previews

The Files pane shows Host, Agent, and intermediate Work locations separately. It lists only formal session deliverables and exposes preview, download, and browser-open actions.

Preview parsing moves out of `FilePreviewScreen` into a backend-neutral service shared by TUI and Web. Supported families remain:

- source and text with syntax/line metadata;
- Markdown and CSV;
- PDF;
- Word, Excel, and PowerPoint;
- Jupyter notebooks;
- images;
- zip/tar/gzip archives;
- PDB/CIF/ENT structures;
- STL/OBJ/glTF/GLB metadata.

The Web uses browser-native PDF/image rendering where possible and the shared structured fallback otherwise. Text extraction retains the TUI's 500,000-byte preview limit and labels truncation; the full file remains downloadable.

### Diff and revert

Diff is hidden when there are no session changes. It appears as soon as the journal reports changes and is selected at completion when changes exist; Files is selected otherwise.

It shows the complete session-baseline unified diff, per-file and aggregate added/deleted counts, creates/deletes with `/dev/null` headers, and a warning for observed-only shell-scan paths. Revert requires a second confirmation, calls `revert_all()`, reports exactly what was restored, and separately lists files deliberately left alone.

### Settings, configuration, and onboarding

F2 opens the five TUI settings groups:

- Appearance;
- Workflow;
- Behavior;
- Permissions;
- Sessions.

Behavior includes plan mode and full thinking. Permissions includes bypass, Auto for me, and a read-only list of saved allow/deny rules. Sessions supports current/recent selection, resume, new, fork, and rename. Workflow switching warns that context resets. Model switching keeps context.

The native-ai-ui light/dark tokens remain the design-system baseline. TUI palettes are additional semantic-token presets rather than ad hoc component colors; mono becomes a high-contrast monochrome Web preset. Theme, workflow, behavior, and permission preferences persist through `UserSettings`.

If runtime configuration is invalid, the Web UI shows the same secret-free local probe and deployment guidance as TUI onboarding. It may show configured variable names, provider, model, endpoint host, and readiness, but never a key value or full sensitive URL. `/config`, `/context`, `/cost`, and `/log` open copyable diagnostics backed by shared query actions.

`/exit` leaves the current Web interaction and explains that the local service remains running. It does not add a remote process-termination endpoint.

## Safety and Error Handling

- The manager serializes session mutations and maintains a revision. Run, steer, interrupt, and approval have explicit concurrency rules.
- Only steer, interrupt, and approval are valid during a run; other mutations return 409 and leave state unchanged.
- Interrupt cancels pending approval, marks active Activity records interrupted, finishes the active stream, and persists the last completed turn.
- Approval IDs are idempotent. Missing, expired, or already-resolved IDs return 404/409 and trigger a state refresh rather than optimistic success.
- Actions build fallible derived state before mutating the active session, following existing mode/resume behavior.
- File and raw-preview routes accept only current workspace, current session inputs/outputs, and discovered run roots. An absolute host path becomes readable only after the explicit attach action copies it into session inputs.
- Relative attach paths cannot escape the workspace; symlink-containing attachments retain the existing refusal semantics.
- Browser uploads stream to a temporary file and then use `AttachmentManager`; the default limit is 100 MiB per file and 500 MiB per request, configurable by environment. Larger local files remain attachable by host path.
- Default CORS is same-origin. A future remote deployment may opt into explicit origins; wildcard credentialed CORS is removed.
- All configuration payloads are constructed from `RuntimeConfigStatus` or `OnboardingProbe`, never `ModelConfig.api_key`.
- UI errors carry a stable code, actionable message, and retryability. Errors never promote partial work to a final report.

## Compatibility and Persistence

- Existing REST paths and named SSE event names remain usable while the Web client migrates.
- Existing persisted `history`, `display_history`, `workflow_turns`, journal, plan, todos, and `tui` fields continue to load.
- Durable cross-surface presentation state is stored in a new versioned UI field; old TUI-only sub-agent snapshots are upgraded in memory.
- New, fork, resume, workflow, cwd, model, settings, and clear operations persist after a successful state transition.
- The Web UI remains `apodex/web_static/index.html`, loaded through CDN React/Tailwind/marked as today, with no repository build step.
- The native-ai-ui skill's token and interaction principles remain the Web design source of truth.
- FastAPI, Uvicorn, sse-starlette, and python-multipart become explicit project dependencies instead of being available only through the current lockfile's transitive graph.

## Delivery Slices

### Slice 1 — Shared contract and broken core actions

- Add CommandRegistry, SessionActions, SessionSnapshot, action/API tests, and event IDs.
- Delegate current REST routes to shared actions.
- Correct steer, revert, clear, new, resume, and atomic state recovery.
- Keep the current page runnable at the end of the slice.

### Slice 2 — Safety, lifecycle, and settings

- Complete WebRenderer callbacks and semantic presentation state.
- Implement all approval branches and plan review.
- Add model/workflow/cwd/session actions, fork/rename/compact, persistent behavior/permission settings, and secret-free diagnostics.

### Slice 3 — Composer and transcript

- Add capabilities-driven command palette/slash completion.
- Add attachments, upload/paste/drop, `@` completion, and input history.
- Add steer/interrupt status, transcript filters/search/review/report behavior, bounded rendering, and follow-tail state.

### Slice 4 — Work panes and previews

- Replace the right tabs with Plan, Activity, Files, and conditional Diff.
- Add normalized task boards, sub-agent detail, complete Activity history, full diff/revert reporting, and all preview families.

### Slice 5 — Onboarding, themes, responsive polish, and documentation

- Add secret-free onboarding and palette presets.
- Complete keyboard/focus, medium/narrow layouts, reconnect UX, offline recovery, and accessibility labels.
- Update root/Web documentation and run full verification.

Each slice must leave a working Web UI and cannot defer a known broken behavior behind a visible control.

## Testing Strategy

Implementation follows test-driven development.

### Shared actions and API

- Unit tests for every action's success, validation, busy conflict, persistence, and rollback-on-failure behavior.
- Route tests for status codes and response shapes using an isolated manager/session fixture.
- Explicit regressions for `SteerInbox.enqueue()`, `WorkspaceJournal.revert_all()`, complete clear semantics, save-before-new/fork, and restored session state.
- Security tests for secret redaction, path traversal, symlink escape, raw-file boundaries, and dangerous server-side confirmation.

### Renderer, events, and reconnect

- Tests assert every renderer method used by `TerminalObserver` produces a semantic event rather than terminal output.
- Named SSE tests assert `{type, data, timestamp}`, monotonically increasing IDs, replay-after-ID, and snapshot fallback after a history gap.
- Tests cover pending/resolved/expired approval recovery, event ordering, task interruption, incomplete/error rendering, todo/task-board updates, and sub-agent snapshots.

### Command and interaction parity

- A command-inventory contract test compares the canonical registry, TUI palette, and `/api/capabilities` output.
- Client command parser/reducer tests cover slash disambiguation, arguments, busy rejection, `@` completion, input history, board projection, event de-duplication, and session reset.
- Pure JavaScript logic stays inside a marked non-JSX region of `index.html`; Node's built-in test runner extracts and evaluates that region, preserving the single-file/no-build architecture.

### Previews

- The existing TUI preview fixtures become shared-service fixtures for text, Markdown, CSV, PDF, Office, notebook, image, archive, PDB, and 3D formats.
- TUI and Web adapter tests assert equivalent preview classification, truncation, metadata, and errors.

### Verification

For each slice:

- run its focused pytest files;
- run affected TUI regressions;
- run JavaScript reducer/parser tests;
- start FastAPI locally and smoke REST plus named SSE;
- load the single-file UI and exercise its changed path.

Before completion:

- run the complete `apodex` test suite;
- run Ruff and Pyright over the enforced project scope;
- validate HTML/JavaScript syntax;
- perform a desktop and narrow-width parity walkthrough against the acceptance matrix below;
- verify the diff touches no unrelated user changes.

## Acceptance Matrix

The work is complete only when all rows pass on the Web UI:

1. Task submit, streaming thinking/content, tool lifecycle, final/incomplete/error state.
2. Live steer queue, queued counter, injection, late follow-up, and interrupt/resume.
3. All ordinary, dangerous, persistent, auto, reject, and redirect approval paths.
4. Plan mode mutation lock, visible plan body, approve, revise, and persisted mode.
5. ReAct todos and Agent Team board/sub-agents.
6. Complete Activity states, details, grouping, counts, and interruption settlement.
7. Complete Files locations/listing and every TUI preview family.
8. Session-baseline Diff, counts, observed-only warning, and attributed revert.
9. New, fork, rename, list, resume, clear, workflow, model, cwd, compact, config, context/cost, log, init, settings, and exit adaptation.
10. Attach, list, detach, upload/paste/drop, `@` search/completion, multiline/large paste, and input history.
11. Transcript filters, search, review navigation, expand/collapse, report jump/copy, and long-session windowing.
12. Help, command palette, full slash registry, TUI/browser-safe shortcuts, theme presets, responsive layout, and focus recovery.
13. First-run secret-free guidance, offline/reconnect recovery, snapshot/event de-duplication, and session persistence.

No row may be marked complete solely because a button or endpoint exists; its behavior must be exercised through the real shared session path.

## Resolved Decisions

- Use dual-entry complete migration.
- Use a shared capability layer rather than a thin text-command bridge or Web-only duplicate implementation.
- Align the right workspace with TUI's Plan/Activity/Files/Diff tabs.
- Preserve named SSE and the existing JSON envelope; add SSE IDs for ordering/reconnect.
- Keep the single-file/no-build Web architecture.
- Use browser-native equivalents where they preserve or improve the same capability.
- Do not add host-server shutdown to `/exit`.
