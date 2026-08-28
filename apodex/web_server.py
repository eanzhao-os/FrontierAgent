"""FastAPI Web Server for FrontierAgent Web UI.

Provides REST and SSE endpoints for native-ai-ui to control FrontierAgent,
stream thoughts, tool calls, task boards, and handle human-in-the-loop approvals.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import json
import logging
import os
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

STATIC_DIR = Path(__file__).resolve().parent / "web_static"

from apodex.cli import apply_model_overrides, publish_model_overrides
from apodex.commands import capabilities_payload
from apodex.profiles import get_profile, profile_names, terminal_mode_names
from apodex.session import TerminalSession, new_session_id
from apodex.session_actions import ActionResult, HTTP_STATUS, SessionActions
from apodex.session_snapshot import build_session_snapshot, transcript_page
from apodex.web_observer import EventBroadcaster, WebApprover, WebEvent, WebRenderer

# Load environment variables
load_dotenv(".env", override=False)
found_env = find_dotenv(usecwd=True)
if found_env:
    load_dotenv(found_env, override=False)

logger = logging.getLogger(__name__)

app = FastAPI(title="FrontierAgent Web API", version="0.1.0")


class ApiError(Exception):
    """HTTP error whose JSON body is `{status, code, message}` at the top level."""

    def __init__(self, code: str, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = HTTP_STATUS.get(code, 500) if status_code is None else status_code


@app.exception_handler(ApiError)
async def api_error_handler(_request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"status": "error", "code": exc.code, "message": exc.message},
    )


def action_http(result: ActionResult) -> dict[str, Any]:
    if result.ok:
        return {"status": "ok", **result.data, "message": result.message}
    raise ApiError(result.code, result.message)


def snapshot_or_replay(bus: EventBroadcaster, last_id: int) -> str | list[WebEvent]:
    replayed = bus.replay_after(last_id)
    if replayed is None:
        return "snapshot_required"
    return replayed


def _last_event_id(request: Request) -> int | None:
    raw = request.headers.get("last-event-id") or request.query_params.get("last_event_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except ValueError:
        return None


_DISPATCHABLE_ACTIONS = frozenset({
    "new_session",
    "fork_session",
    "clear_context",
    "revert_changes",
    "rename_session",
    "resume_session",
    "set_plan_mode",
    "set_verbose",
    "set_auto_approve",
    "set_auto_for_me",
    "switch_workflow",
    "switch_model",
    "change_cwd",
})


# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:3030", "http://localhost:3030"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    prompt: str
    mode: Optional[str] = None


class ModeRequest(BaseModel):
    mode: str


class NewSessionRequest(BaseModel):
    mode: str = "react"


class WorkspaceRequest(BaseModel):
    path: str


class ApproveRequest(BaseModel):
    id: str
    decision: str
    feedback: str = ""
    confirmation: str = ""


class SteerRequest(BaseModel):
    instruction: str


class ActionRequest(BaseModel):
    action: str
    arguments: dict[str, Any] = {}
    expected_revision: int | None = None


class AttachPathRequest(BaseModel):
    paths: list[str]


_WORKSPACE_SEARCH_SKIP_DIRS = frozenset({
    ".apodex", ".git", ".hg", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".svn", ".tox", ".venv", "__pycache__", "build", "dist", "node_modules",
    "target", "venv",
})
_WORKSPACE_SEARCH_LIMIT = 20_000


class WebAgentManager:
    BUSY_ALLOWED = frozenset({"steer", "interrupt", "approve"})

    def __init__(self, initial_mode: str = "react", cwd: Optional[str] = None) -> None:
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.mode = initial_mode if initial_mode in terminal_mode_names() else "react"
        self.broadcaster = EventBroadcaster()
        self.approver = WebApprover(self.broadcaster)
        self.renderer = WebRenderer(self.broadcaster)
        self.active_task: Optional[asyncio.Task[None]] = None
        self.is_running = False
        self.session: Optional[TerminalSession] = None
        self.revision = 0
        self._lock = asyncio.Lock()
        self._init_session(self.mode)

    def _init_session(self, mode: str, session_id: Optional[str] = None) -> None:
        from apodex.native import prepare_native_runtime
        from apodex.sandbox import NATIVE, Strategy, set_active_strategy

        profile = get_profile(mode)
        cfg = dataclasses.replace(profile.model_config)
        apply_model_overrides(cfg)
        publish_model_overrides(cfg)
        max_turns = profile.max_turns or 50

        sid = session_id or new_session_id(mode)
        prepare_native_runtime(self.cwd, sid)
        set_active_strategy(Strategy(NATIVE, "workspace-local native runtime"))

        self.session = TerminalSession(
            cfg=cfg,
            cwd=self.cwd,
            renderer=self.renderer,  # type: ignore
            auto_approve=False,
            max_turns=max_turns,
            interactive=True,
            mode=mode,
            session_id=sid,
        )
        self.session.tui_mode = True
        self.session.approver = self.approver  # type: ignore
        self.mode = mode

    def switch_mode(self, new_mode: str) -> None:
        if new_mode not in terminal_mode_names():
            raise ValueError(f"Unknown mode '{new_mode}'. Choose from {terminal_mode_names()}")
        if self.is_running:
            raise RuntimeError("Cannot switch mode while a task is running.")
        self._init_session(new_mode)

    async def run(self, prompt: str, mode: Optional[str] = None) -> None:
        if self.is_running:
            raise RuntimeError("Agent is already busy running a task.")
        if mode and mode != self.mode:
            self.switch_mode(mode)

        if not self.session:
            self._init_session(self.mode)

        self.is_running = True
        await self.broadcaster.emit(
            "task_started",
            {"prompt": prompt, "mode": self.mode, "session_id": self.session.session_id},
        )

        async def _execute() -> None:
            try:
                assert self.session is not None
                await self.session.run_task(prompt)
            except asyncio.CancelledError:
                await self.broadcaster.emit("task_cancelled", {"message": "Task was interrupted by user."})
            except Exception as exc:
                logger.exception("Error running task in session")
                await self.broadcaster.emit("error", {"message": str(exc)})
            finally:
                self.is_running = False
                await self.broadcaster.emit("task_ended", {"status": "idle"})

        self.active_task = asyncio.create_task(_execute())

    def steer(self, instruction: str) -> bool:
        if not self.is_running or not self.session or self.session._inbox is None:
            return False
        self.session._inbox.enqueue(instruction)
        return True

    def require_idle(self, action: str) -> None:
        if self.is_running and action not in self.BUSY_ALLOWED:
            raise ApiError("busy", "A task is running. Interrupt first.")

    def settle_interrupt(self) -> None:
        from apodex.observers import Decision

        for fut in list(self.approver._pending.values()):
            if not fut.done():
                fut.set_result(Decision(False))
        renderer = self.renderer
        interrupted = getattr(renderer, "interrupted", None)
        if callable(interrupted):
            interrupted()

    def interrupt(self) -> bool:
        self.settle_interrupt()
        if self.is_running and self.active_task and not self.active_task.done():
            self.active_task.cancel()
            self.is_running = False
            return True
        return False


# Global manager instance
manager: Optional[WebAgentManager] = None


def get_manager() -> WebAgentManager:
    global manager
    if manager is None:
        init_mode = os.environ.get("FRONTIER_AGENT_MODE", "react")
        init_cwd = os.environ.get("FRONTIER_AGENT_CWD", os.getcwd())
        manager = WebAgentManager(initial_mode=init_mode, cwd=init_cwd)
    return manager


@app.get("/api/capabilities")
async def get_capabilities() -> dict[str, Any]:
    return capabilities_payload()


@app.get("/api/state")
async def get_state() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    return build_session_snapshot(
        mgr.session,
        revision=mgr.revision,
        sequence=mgr.broadcaster.sequence,
        runtime_status="running" if mgr.is_running else "ready",
        pending_approval=mgr.approver.pending_snapshot(),
    )


def _runtime_config_payload(session: TerminalSession) -> dict[str, Any]:
    status = session.runtime_config_status()
    rules = getattr(session, "rules", None)
    settings = getattr(session, "user_settings", None)
    return {
        "ok": bool(status.ok),
        "mode": status.mode,
        "profile_name": status.profile_name,
        "profile_path": status.profile_path,
        "provider": status.provider,
        "model": status.model,
        "endpoint_host": status.endpoint_host,
        "api_key_env": status.api_key_env,
        "api_key_configured": status.api_key_configured,
        "issues": [
            {
                "code": issue.code,
                "message": issue.message,
                "env_var": issue.env_var,
                "blocking": issue.blocking,
            }
            for issue in status.issues
        ],
        "cwd": session.cwd,
        "models": list(getattr(session, "models", None) or []),
        "modes": terminal_mode_names(),
        "verbose": bool(getattr(session, "verbose", False)),
        "plan_mode": bool(getattr(getattr(session, "plan_state", None), "active", False)),
        "auto_approve": bool(getattr(getattr(session, "approver", None), "auto_approve", False)),
        "auto_for_me": bool(getattr(getattr(session, "approver", None), "auto_for_me", False)),
        "theme": getattr(settings, "theme", "") if settings is not None else "",
        "permissions": {
            "allow": sorted(getattr(rules, "allow", set()) or []),
            "deny": sorted(getattr(rules, "deny", set()) or []),
        },
    }


@app.get("/api/config")
async def get_config() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    return _runtime_config_payload(mgr.session)


@app.get("/api/context")
async def get_context() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    result = SessionActions(mgr.session).context_cost()
    return {"status": "ok", "report": result.message}


@app.get("/api/log")
async def get_log() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    result = SessionActions(mgr.session).trace_path()
    return {"status": "ok", "path": result.data.get("path", "")}


def _attachment_item(item: Any) -> dict[str, Any]:
    return {
        "relative_path": item.relative_path,
        "agent_path": item.agent_path,
        "size": item.size,
    }


def _upload_byte_limits() -> tuple[int, int]:
    file_mib = float(os.environ.get("APODEX_WEB_UPLOAD_MAX_FILE_MIB", "100"))
    request_mib = float(os.environ.get("APODEX_WEB_UPLOAD_MAX_REQUEST_MIB", "500"))
    return int(file_mib * 1024 * 1024), int(request_mib * 1024 * 1024)


@app.get("/api/attachments")
async def list_attachments() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    return {"attachments": [_attachment_item(item) for item in mgr.session.attachments.list()]}


@app.post("/api/attachments/path")
async def attach_host_paths(req: AttachPathRequest) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        if not mgr.session:
            mgr._init_session(mgr.mode)
        assert mgr.session is not None
        try:
            added = mgr.session.attachments.attach_many(req.paths)
        except Exception as exc:
            raise ApiError("validation", str(exc))
        mgr.revision += 1
        return {
            "status": "ok",
            "attachments": [_attachment_item(item) for item in added],
        }


@app.post("/api/attachments/upload")
async def upload_attachments(files: list[UploadFile] = File(...)) -> dict[str, Any]:
    mgr = get_manager()
    max_file, max_request = _upload_byte_limits()
    total = 0
    temp_paths: list[str] = []
    temp_dirs: list[str] = []
    try:
        for upload in files:
            data = await upload.read()
            total += len(data)
            if len(data) > max_file or total > max_request:
                raise HTTPException(status_code=413, detail="Upload too large")
            folder = tempfile.mkdtemp(prefix="apodex-upload-")
            temp_dirs.append(folder)
            name = Path(upload.filename or "upload.bin").name or "upload.bin"
            dest = Path(folder) / name
            dest.write_bytes(data)
            temp_paths.append(str(dest))
        async with mgr._lock:
            if not mgr.session:
                mgr._init_session(mgr.mode)
            assert mgr.session is not None
            added = mgr.session.attachments.attach_many(temp_paths)
            mgr.revision += 1
        return {"status": "ok", "attachments": [_attachment_item(item) for item in added]}
    finally:
        for folder in temp_dirs:
            shutil.rmtree(folder, ignore_errors=True)


@app.delete("/api/attachments/{name:path}")
async def detach_attachment(name: str) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        if not mgr.session:
            mgr._init_session(mgr.mode)
        assert mgr.session is not None
        result = SessionActions(mgr.session).detach_attachment(name)
        mgr.revision += 1
        return action_http(result)


@app.get("/api/files/search")
async def search_files(q: str = "") -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    needle = (q or "").strip().lower()
    candidates: list[dict[str, str]] = []
    for item in mgr.session.attachments.list():
        if not needle or needle in item.relative_path.lower():
            candidates.append({"path": item.relative_path, "source": "attachment"})
    cwd = Path(mgr.cwd)
    walked = 0
    if cwd.is_dir():
        stack = [cwd]
        while stack and walked < _WORKSPACE_SEARCH_LIMIT:
            current = stack.pop()
            try:
                entries = list(os.scandir(current))
            except OSError:
                continue
            for entry in entries:
                if walked >= _WORKSPACE_SEARCH_LIMIT:
                    break
                try:
                    if entry.is_dir(follow_symlinks=False):
                        if entry.name not in _WORKSPACE_SEARCH_SKIP_DIRS:
                            stack.append(Path(entry.path))
                        continue
                    if not entry.is_file(follow_symlinks=False):
                        continue
                except OSError:
                    continue
                walked += 1
                rel = os.path.relpath(entry.path, cwd).replace(os.sep, "/")
                if needle and needle not in rel.lower():
                    continue
                candidates.append({"path": rel, "source": "workspace"})
    return {"candidates": candidates}


@app.post("/api/actions")
async def post_action(req: ActionRequest) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        if req.action not in _DISPATCHABLE_ACTIONS:
            raise ApiError("validation", f"unknown action '{req.action}'")
        if req.expected_revision is not None and req.expected_revision != mgr.revision:
            raise ApiError("revision_conflict", "session changed; reload snapshot")
        mgr.require_idle(req.action)
        if not mgr.session:
            mgr._init_session(mgr.mode)
        assert mgr.session is not None
        actions = SessionActions(mgr.session)
        args = req.arguments or {}
        if req.action == "new_session":
            result = actions.new_session(fork=False)
            mgr.broadcaster.clear()
        elif req.action == "fork_session":
            result = actions.new_session(fork=True)
        elif req.action == "clear_context":
            result = actions.clear_context()
        elif req.action == "revert_changes":
            result = actions.revert_changes()
        elif req.action == "rename_session":
            result = actions.rename_session(str(args.get("name", "")))
        elif req.action == "resume_session":
            result = actions.resume_session(str(args.get("session_id", "")))
        elif req.action == "set_plan_mode":
            result = actions.set_plan_mode(bool(args.get("active")))
        elif req.action == "set_verbose":
            enabled = args.get("enabled", args.get("verbose"))
            result = actions.set_verbose(bool(enabled))
        elif req.action == "set_auto_approve":
            enabled = args.get("enabled", args.get("auto_approve"))
            result = actions.set_auto_approve(bool(enabled))
        elif req.action == "set_auto_for_me":
            enabled = args.get("enabled", args.get("auto_for_me"))
            result = actions.set_auto_for_me(bool(enabled))
        elif req.action == "switch_workflow":
            result = actions.switch_workflow(str(args.get("mode") or args.get("name") or ""))
        elif req.action == "switch_model":
            result = actions.switch_model(str(args.get("model") or args.get("target") or ""))
        else:
            result = actions.change_cwd(str(args.get("path") or args.get("cwd") or ""))
        payload = action_http(result)
        mgr.mode = mgr.session.mode
        mgr.cwd = mgr.session.cwd
        mgr.revision += 1
        return payload


@app.get("/api/status")
async def get_status() -> dict[str, Any]:
    mgr = get_manager()
    session = mgr.session
    if not session:
        return {"status": "uninitialized"}

    history_count = len(session.display_history) if session.display_history else len(session.history)
    return {
        "status": "running" if mgr.is_running else "ready",
        "mode": mgr.mode,
        "model": session.cfg.model,
        "base_url": session.cfg.base_url or "default",
        "cwd": session.cwd,
        "session_id": session.session_id,
        "modes_available": terminal_mode_names(),
        "usage": {
            "prompt_tokens": getattr(session.usage, "prompt_tokens", 0),
            "completion_tokens": getattr(session.usage, "completion_tokens", 0),
            "total_tokens": getattr(session.usage, "total_tokens", 0),
            "turns": len(session.workflow_turns) if session.workflow_turns else history_count,
        },
    }


@app.post("/api/mode")
async def set_mode(req: ModeRequest) -> dict[str, Any]:
    mgr = get_manager()
    try:
        mgr.switch_mode(req.mode)
        await mgr.broadcaster.emit("mode_changed", {"mode": mgr.mode})
        return {"status": "ok", "mode": mgr.mode}
    except Exception as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/api/run")
async def run_task(req: RunRequest) -> dict[str, Any]:
    mgr = get_manager()
    if mgr.is_running:
        raise HTTPException(status_code=409, detail="A task is already running.")
    try:
        await mgr.run(req.prompt, req.mode)
        return {"status": "started", "mode": mgr.mode}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc))


@app.post("/api/steer")
async def steer_task(req: SteerRequest) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        success = mgr.steer(req.instruction)
        if success:
            mgr.revision += 1
            await mgr.broadcaster.emit("steer_injected", {"instruction": req.instruction})
            return {"status": "ok", "injected": True}
        return {"status": "ignored", "injected": False, "reason": "No running task or inbox"}


@app.post("/api/approve")
async def approve_tool(req: ApproveRequest) -> dict[str, Any]:
    mgr = get_manager()
    allowed = {
        "approve",
        "reject",
        "redirect",
        "auto_for_me",
        "allow_session",
        "always_allow",
    }
    if req.decision not in allowed:
        raise ApiError("validation", f"unknown decision '{req.decision}'")
    fut = mgr.approver._pending.get(req.id)
    if fut is None:
        raise ApiError("not_found", "Pending approval not found or expired.")
    if fut.done():
        raise ApiError("busy", "Approval already resolved.")
    info = mgr.approver._pending_info.get(req.id) or {}
    dangerous = str(info.get("dangerous") or "").strip()
    if req.decision == "approve" and dangerous and req.confirmation != "yes":
        raise ApiError("dangerous_confirmation", "Type yes to confirm this dangerous action.")
    resolved = mgr.approver.resolve(
        req.id,
        decision=req.decision,
        feedback=req.feedback,
        confirmation=req.confirmation,
    )
    if resolved:
        await mgr.broadcaster.emit(
            "approval_resolved",
            {"id": req.id, "decision": req.decision, "feedback": req.feedback},
        )
        return {"status": "ok", "resolved": True}
    raise ApiError("not_found", "Pending approval not found or expired.")


@app.post("/api/interrupt")
async def interrupt_task() -> dict[str, Any]:
    mgr = get_manager()
    stopped = mgr.interrupt()
    return {"status": "interrupted" if stopped else "noop"}


@app.get("/api/events")
async def stream_events(request: Request) -> EventSourceResponse:
    mgr = get_manager()
    last_id = _last_event_id(request)
    gap = last_id is not None and snapshot_or_replay(mgr.broadcaster, last_id) == "snapshot_required"
    queue = await mgr.broadcaster.subscribe(None if gap else last_id)

    async def event_generator() -> AsyncGenerator[dict[str, Any], None]:
        try:
            if gap:
                yield {
                    "event": "snapshot_required",
                    "data": json.dumps(
                        {
                            "type": "snapshot_required",
                            "data": {"reason": "gap"},
                            "timestamp": time.time(),
                        },
                        ensure_ascii=False,
                    ),
                }
            elif last_id is None:
                yield {
                    "event": "connected",
                    "data": json.dumps({"session_id": mgr.session.session_id if mgr.session else ""}),
                }
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                    yield {
                        "id": str(event.sequence),
                        "event": event.event_type,
                        "data": json.dumps(
                            {"type": event.event_type, "data": event.data, "timestamp": event.timestamp},
                            ensure_ascii=False,
                        ),
                    }
                except asyncio.TimeoutError:
                    # Keep-alive ping
                    yield {"event": "ping", "data": "{}"}
        finally:
            await mgr.broadcaster.unsubscribe(queue)

    return EventSourceResponse(event_generator())


@app.get("/api/transcript")
async def get_transcript(before: str | None = None) -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        mgr._init_session(mgr.mode)
    assert mgr.session is not None
    return transcript_page(mgr.session, before=before)


@app.get("/api/history")
async def get_history() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        return {"messages": []}
    return {
        "display_history": mgr.session.display_history,
        "workflow_turns": [
            dataclasses.asdict(turn) if hasattr(turn, "__dataclass_fields__") else turn
            for turn in mgr.session.workflow_turns
        ],
    }


@app.get("/api/diff")
async def get_diff() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session or not mgr.session.journal:
        return {"diff": "", "stats": {}}
    stats = mgr.session.journal.revertable_diffstat()
    return {"stats": stats}


def normalize_session_id(sid: Optional[str]) -> Optional[str]:
    if not sid:
        return None
    return sid.strip().replace(" ", "+")


@app.get("/api/artifacts")
async def list_artifacts(session_id: Optional[str] = None) -> dict[str, Any]:
    from apodex.session_state import discover_all_run_roots, load_session_state

    mgr = get_manager()
    raw_sid = normalize_session_id(session_id)
    sid = raw_sid or (mgr.session.session_id if mgr.session else None)
    if not sid:
        return {"artifacts": [], "session_id": None}

    # Locate the exact run directory for this session
    state = load_session_state(sid) or {}
    run_dir = None
    if "_run_dir" in state and Path(state["_run_dir"]).is_dir():
        run_dir = Path(state["_run_dir"])
    if not run_dir:
        for root in discover_all_run_roots():
            candidate = root / sid
            if candidate.is_dir():
                run_dir = candidate
                break
    if not run_dir:
        run_dir = Path(mgr.cwd) / ".apodex" / "runs" / sid

    artifacts: list[dict[str, Any]] = []
    outputs_dir = run_dir / "outputs"

    if outputs_dir.exists() and outputs_dir.is_dir():
        for p in sorted(outputs_dir.rglob("*")):
            if p.is_file():
                if p.name in (".DS_Store", "__pycache__", "session.json", "engine.log", "trace.jsonl"):
                    continue
                artifacts.append({
                    "name": str(p.relative_to(outputs_dir)),
                    "category": "outputs",
                    "size": p.stat().st_size,
                    "path": str(p.resolve()),
                    "is_md": p.suffix.lower() in (".md", ".markdown"),
                    "session_id": sid,
                    "modified_at": p.stat().st_mtime,
                })

    # Sort newest first
    artifacts.sort(key=lambda a: -a["modified_at"])

    return {"artifacts": artifacts, "session_id": sid}


@app.get("/api/file")
async def read_file_content(path: str) -> dict[str, Any]:
    from apodex.session_state import discover_all_run_roots
    from apodex.web_paths import allowed_file_path

    mgr = get_manager()
    session = mgr.session
    inputs_dir = None
    outputs_dir = os.environ.get("FRONTIER_AGENT_OUTPUTS_DIR")
    if session is not None:
        attachments = getattr(session, "attachments", None)
        inputs_dir = str(getattr(attachments, "agent_dir", "") or "") or None
    file_path = allowed_file_path(
        path,
        cwd=mgr.cwd,
        session_id=session.session_id if session else "",
        run_roots=discover_all_run_roots(),
        inputs_dir=inputs_dir,
        outputs_dir=outputs_dir,
    )

    if file_path is None or not file_path.is_file():
        raise HTTPException(status_code=403, detail="Access denied or file not found.")

    try:
        content = file_path.read_text(encoding="utf-8", errors="replace")
        is_md = file_path.suffix.lower() in (".md", ".markdown")
        return {
            "name": file_path.name,
            "path": str(file_path),
            "content": content,
            "is_md": is_md,
            "size": file_path.stat().st_size,
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Could not read file: {exc}")


@app.post("/api/revert")
async def revert_changes() -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        mgr.require_idle("revert")
        if not mgr.session or not mgr.session.journal:
            return {"status": "noop"}
        result = SessionActions(mgr.session).revert_changes()
        mgr.revision += 1
        await mgr.broadcaster.emit("revert", {"reverted_files": result.data.get("reverted", [])})
        return action_http(result)


@app.post("/api/clear")
async def clear_session() -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        mgr.require_idle("clear")
        if not mgr.session:
            return {"status": "ok"}
        result = SessionActions(mgr.session).clear_context()
        mgr.revision += 1
        await mgr.broadcaster.emit("cleared", {})
        return action_http(result)


@app.get("/api/sessions")
async def list_all_sessions() -> dict[str, Any]:
    from apodex.session_state import discover_all_run_roots, list_saved_sessions, load_session_state

    mgr = get_manager()
    saved = list_saved_sessions(workspace=mgr.cwd)
    results = []
    for s in saved:
        sid = s["session_id"]
        detail = load_session_state(sid) or {}
        
        # Locate run dir
        run_dir_str = s.get("run_dir") or detail.get("_run_dir")
        run_dir = Path(run_dir_str) if run_dir_str else None
        if not run_dir or not run_dir.is_dir():
            for root in discover_all_run_roots():
                candidate = root / sid
                if candidate.is_dir():
                    run_dir = candidate
                    break
        if not run_dir:
            run_dir = Path(mgr.cwd) / ".apodex" / "runs" / sid

        outputs_dir = run_dir / "outputs"
        out_count = len([p for p in outputs_dir.rglob("*") if p.is_file() and p.name not in (".DS_Store", "__pycache__")]) if outputs_dir.exists() else 0
        journal = detail.get("journal", {})
        file_count = len(journal) if isinstance(journal, dict) else 0

        # Workspace name
        ws_name = Path(s.get("cwd") or mgr.cwd).name

        results.append({
            "session_id": sid,
            "name": s.get("name") or "",
            "mode": s.get("mode") or "react",
            "cwd": s.get("cwd") or str(run_dir.parent.parent),
            "workspace_name": ws_name,
            "message_count": s.get("message_count", 0),
            "modified_at": s.get("modified_at", ""),
            "model": detail.get("model", ""),
            "file_count": file_count,
            "outputs_count": out_count,
            "run_dir": str(run_dir.resolve()),
            "is_current": bool(mgr.session and mgr.session.session_id == sid),
        })
    return {"sessions": results}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str) -> dict[str, Any]:
    from apodex.session_state import discover_all_run_roots, load_session_state

    mgr = get_manager()
    session_id = normalize_session_id(session_id) or session_id
    state = load_session_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")

    run_dir_str = state.get("_run_dir")
    run_dir = Path(run_dir_str) if run_dir_str else None
    if not run_dir or not run_dir.is_dir():
        for root in discover_all_run_roots():
            candidate = root / session_id
            if candidate.is_dir():
                run_dir = candidate
                break
    if not run_dir:
        run_dir = Path(mgr.cwd) / ".apodex" / "runs" / session_id

    outputs_dir = run_dir / "outputs"
    workspace_dir = run_dir / "workspace"

    outputs: list[dict[str, Any]] = []
    if outputs_dir.exists():
        for p in sorted(outputs_dir.rglob("*")):
            if p.is_file() and p.name not in (".DS_Store", "__pycache__"):
                outputs.append({
                    "name": str(p.relative_to(outputs_dir)),
                    "size": p.stat().st_size,
                    "path": str(p.resolve()),
                    "is_md": p.suffix.lower() in (".md", ".markdown"),
                })

    workspace_files: list[dict[str, Any]] = []
    if workspace_dir.exists():
        for p in sorted(workspace_dir.rglob("*")):
            if p.is_file() and p.name not in (".DS_Store", "__pycache__"):
                workspace_files.append({
                    "name": str(p.relative_to(workspace_dir)),
                    "size": p.stat().st_size,
                    "path": str(p.resolve()),
                    "is_md": p.suffix.lower() in (".md", ".markdown"),
                })

    return {
        "session_id": session_id,
        "state": state,
        "outputs": outputs,
        "workspace_files": workspace_files,
        "run_dir": str(run_dir.resolve()),
    }


@app.post("/api/sessions/{session_id}/resume")
async def resume_saved_session(session_id: str) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        mgr.require_idle("resume")
        session_id = normalize_session_id(session_id) or session_id
        if not mgr.session:
            mgr._init_session(mgr.mode)
        assert mgr.session is not None
        result = SessionActions(mgr.session).resume_session(session_id)
        payload = action_http(result)
        mgr.cwd = mgr.session.cwd
        mgr.mode = mgr.session.mode
        mgr.revision += 1
        await mgr.broadcaster.emit(
            "session_resumed",
            {"session_id": mgr.session.session_id, "mode": mgr.mode, "cwd": mgr.cwd},
        )
        payload.update({"mode": mgr.mode, "cwd": mgr.cwd})
        return payload


@app.post("/api/sessions/new")
async def create_new_session(req: NewSessionRequest) -> dict[str, Any]:
    mgr = get_manager()
    async with mgr._lock:
        mgr.require_idle("new")
        mode = req.mode if req.mode in terminal_mode_names() else "react"
        if not mgr.session:
            mgr._init_session(mode)
        assert mgr.session is not None
        result = SessionActions(mgr.session).new_session(fork=False)
        mgr.mode = mgr.session.mode
        mgr.revision += 1
        mgr.broadcaster.clear()
        await mgr.broadcaster.emit(
            "session_created",
            {"session_id": mgr.session.session_id, "mode": mgr.mode},
        )
        payload = action_http(result)
        payload["mode"] = mgr.mode
        return payload


@app.get("/api/workspaces")
async def list_workspaces() -> dict[str, Any]:
    from apodex.workspace_config import load_configured_paths

    mgr = get_manager()
    configured = load_configured_paths()
    results: list[dict[str, Any]] = []

    for path_str in configured:
        p = Path(path_str).resolve()
        runs_dir = p / ".apodex" / "runs"
        count = len(list(runs_dir.glob("*/session.json"))) if runs_dir.is_dir() else 0
        results.append({
            "path": str(p),
            "name": p.name or str(p),
            "exists": p.is_dir(),
            "session_count": count,
            "is_active": str(p) == str(Path(mgr.cwd).resolve()),
        })

    return {"workspaces": results, "active_cwd": str(Path(mgr.cwd).resolve())}


@app.post("/api/workspaces/add")
async def add_workspace(req: WorkspaceRequest) -> dict[str, Any]:
    from apodex.workspace_config import add_workspace_path

    path_str = os.path.expanduser(req.path.strip())
    p = Path(path_str).resolve()
    if not p.exists():
        try:
            p.mkdir(parents=True, exist_ok=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Cannot create directory: {exc}")

    add_workspace_path(str(p))
    mgr = get_manager()
    await mgr.broadcaster.emit("workspaces_updated", {"added": str(p)})
    return {"status": "ok", "path": str(p)}


@app.post("/api/workspaces/remove")
async def remove_workspace(req: WorkspaceRequest) -> dict[str, Any]:
    from apodex.workspace_config import remove_workspace_path

    mgr = get_manager()
    path_str = os.path.expanduser(req.path.strip())
    p = Path(path_str).resolve()
    if str(p) == str(Path(mgr.cwd).resolve()):
        raise HTTPException(status_code=400, detail="Cannot remove currently active workspace.")

    remove_workspace_path(str(p))
    await mgr.broadcaster.emit("workspaces_updated", {"removed": str(p)})
    return {"status": "ok", "path": str(p)}


@app.post("/api/workspaces/select")
async def select_active_workspace(req: WorkspaceRequest) -> dict[str, Any]:
    from apodex.workspace_config import add_workspace_path

    mgr = get_manager()
    if mgr.is_running:
        raise HTTPException(status_code=400, detail="Cannot switch workspace while a task is running.")

    path_str = os.path.expanduser(req.path.strip())
    p = Path(path_str).resolve()
    if not p.is_dir():
        raise HTTPException(status_code=404, detail="Workspace directory does not exist.")

    add_workspace_path(str(p))
    mgr.cwd = str(p)
    mgr._init_session(mgr.mode)
    await mgr.broadcaster.emit("workspace_changed", {"cwd": mgr.cwd, "session_id": mgr.session.session_id})
    return {"status": "ok", "cwd": mgr.cwd, "session_id": mgr.session.session_id}


@app.get("/api/all_files")
async def get_all_work_files() -> dict[str, Any]:
    from apodex.session_state import discover_all_run_roots, list_saved_sessions, load_session_state

    mgr = get_manager()
    saved = list_saved_sessions(workspace=mgr.cwd)
    all_files: list[dict[str, Any]] = []
    all_outputs: list[dict[str, Any]] = []
    for s in saved:
        sid = s["session_id"]
        state = load_session_state(sid) or {}
        journal = state.get("journal") or {}
        if isinstance(journal, dict):
            for fpath in journal.keys():
                all_files.append({
                    "session_id": sid,
                    "file": fpath,
                    "modified_at": s.get("modified_at"),
                    "mode": s.get("mode"),
                })
        
        run_dir_str = s.get("run_dir") or state.get("_run_dir")
        run_dir = Path(run_dir_str) if run_dir_str else None
        if not run_dir or not run_dir.is_dir():
            for root in discover_all_run_roots():
                candidate = root / sid
                if candidate.is_dir():
                    run_dir = candidate
                    break
        if not run_dir:
            run_dir = Path(mgr.cwd) / ".apodex" / "runs" / sid

        out_dir = run_dir / "outputs"
        if out_dir.exists():
            for p in out_dir.rglob("*"):
                if p.is_file() and p.name not in (".DS_Store", "__pycache__"):
                    all_outputs.append({
                        "session_id": sid,
                        "name": str(p.relative_to(out_dir)),
                        "size": p.stat().st_size,
                        "path": str(p.resolve()),
                        "is_md": p.suffix.lower() in (".md", ".markdown"),
                        "modified_at": s.get("modified_at"),
                    })
    return {"journal_files": all_files, "artifacts": all_outputs}


@app.get("/", response_class=HTMLResponse)
@app.get("/frontier", response_class=HTMLResponse)
async def serve_ui() -> Any:
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return HTMLResponse("<h1>FrontierAgent Web UI</h1><p>Static UI file not found.</p>")


def main() -> None:
    parser = argparse.ArgumentParser(description="FrontierAgent Web UI Server")
    parser.add_argument("--mode", default="react", choices=["react", "agent_team"], help="Initial workflow mode")
    parser.add_argument("--host", default="127.0.0.1", help="Host address")
    parser.add_argument("--port", type=int, default=3030, help="Port to listen on")
    parser.add_argument("--cwd", default=None, help="Working directory for agent")
    args = parser.parse_args()

    os.environ["FRONTIER_AGENT_MODE"] = args.mode
    if args.cwd:
        os.environ["FRONTIER_AGENT_CWD"] = os.path.abspath(args.cwd)

    import uvicorn

    print(f"🚀 FrontierAgent Web Backend starting on http://{args.host}:{args.port} (mode: {args.mode})")
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
