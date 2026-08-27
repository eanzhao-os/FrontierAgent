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
import sys
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from dotenv import find_dotenv, load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

STATIC_DIR = Path(__file__).resolve().parent / "web_static"

from apodex.cli import apply_model_overrides, publish_model_overrides
from apodex.profiles import get_profile, profile_names, terminal_mode_names
from apodex.session import TerminalSession, new_session_id
from apodex.web_observer import EventBroadcaster, WebApprover, WebEvent, WebObserver, WebRenderer

# Load environment variables
load_dotenv(".env", override=False)
found_env = find_dotenv(usecwd=True)
if found_env:
    load_dotenv(found_env, override=False)

logger = logging.getLogger(__name__)

app = FastAPI(title="FrontierAgent Web API", version="0.1.0")

# Enable CORS for Next.js frontend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class RunRequest(BaseModel):
    prompt: str
    mode: Optional[str] = None


class ModeRequest(BaseModel):
    mode: str


class ApproveRequest(BaseModel):
    id: str
    approved: bool
    feedback: str = ""
    remember: bool = False
    auto_all: bool = False


class SteerRequest(BaseModel):
    instruction: str


class WebAgentManager:
    def __init__(self, initial_mode: str = "react", cwd: Optional[str] = None) -> None:
        self.cwd = os.path.abspath(cwd or os.getcwd())
        self.mode = initial_mode if initial_mode in terminal_mode_names() else "react"
        self.broadcaster = EventBroadcaster()
        self.approver = WebApprover(self.broadcaster)
        self.renderer = WebRenderer(self.broadcaster)
        self.active_task: Optional[asyncio.Task[None]] = None
        self.is_running = False
        self.session: Optional[TerminalSession] = None
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
        if not self.is_running or not self.session:
            return False
        if hasattr(self.session, "_inbox") and self.session._inbox is not None:
            self.session._inbox.steer(instruction)
            return True
        return False

    def interrupt(self) -> bool:
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
    success = mgr.steer(req.instruction)
    if success:
        await mgr.broadcaster.emit("steer_injected", {"instruction": req.instruction})
        return {"status": "ok", "injected": True}
    return {"status": "ignored", "injected": False, "reason": "No running task or inbox"}


@app.post("/api/approve")
async def approve_tool(req: ApproveRequest) -> dict[str, Any]:
    mgr = get_manager()
    resolved = mgr.approver.resolve(
        req.id,
        approved=req.approved,
        feedback=req.feedback,
        remember=req.remember,
        auto_all=req.auto_all,
    )
    if resolved:
        await mgr.broadcaster.emit(
            "approval_resolved",
            {"id": req.id, "approved": req.approved, "feedback": req.feedback},
        )
        return {"status": "ok", "resolved": True}
    raise HTTPException(status_code=404, detail="Pending approval not found or expired.")


@app.post("/api/interrupt")
async def interrupt_task() -> dict[str, Any]:
    mgr = get_manager()
    stopped = mgr.interrupt()
    return {"status": "interrupted" if stopped else "noop"}


@app.get("/api/events")
async def stream_events(request: Request) -> EventSourceResponse:
    mgr = get_manager()
    queue = await mgr.broadcaster.subscribe()

    async def event_generator() -> AsyncGenerator[dict[str, Any], None]:
        try:
            # Yield initial connect event
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


@app.get("/api/artifacts")
async def list_artifacts() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        return {"artifacts": []}
    outputs_dir = Path(mgr.cwd) / ".apodex" / "runs" / mgr.session.session_id / "outputs"
    artifacts: list[dict[str, Any]] = []
    if outputs_dir.exists():
        for p in outputs_dir.rglob("*"):
            if p.is_file():
                rel = str(p.relative_to(outputs_dir))
                size = p.stat().st_size
                artifacts.append({"name": rel, "size": size, "path": str(p)})
    return {"artifacts": artifacts}


@app.post("/api/revert")
async def revert_changes() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session or not mgr.session.journal:
        return {"status": "noop"}
    reverted = mgr.session.journal.revert()
    await mgr.broadcaster.emit("revert", {"reverted_files": reverted})
    return {"status": "ok", "reverted": reverted}


@app.post("/api/clear")
async def clear_session() -> dict[str, Any]:
    mgr = get_manager()
    if not mgr.session:
        return {"status": "ok"}
    mgr.session.history.clear()
    mgr.session.display_history.clear()
    mgr.session.workflow_turns.clear()
    mgr.broadcaster.clear()
    await mgr.broadcaster.emit("cleared", {})
    return {"status": "ok"}


@app.get("/api/sessions")
async def list_all_sessions() -> dict[str, Any]:
    from apodex.session_state import list_saved_sessions, load_session_state

    mgr = get_manager()
    saved = list_saved_sessions(workspace=mgr.cwd)
    results = []
    for s in saved:
        sid = s["session_id"]
        detail = load_session_state(sid) or {}
        outputs_dir = Path(mgr.cwd) / ".apodex" / "runs" / sid / "outputs"
        out_count = len(list(outputs_dir.rglob("*"))) if outputs_dir.exists() else 0
        journal = detail.get("journal", {})
        file_count = len(journal) if isinstance(journal, dict) else 0
        results.append({
            "session_id": sid,
            "name": s.get("name") or "",
            "mode": s.get("mode") or "react",
            "cwd": s.get("cwd") or mgr.cwd,
            "message_count": s.get("message_count", 0),
            "modified_at": s.get("modified_at", ""),
            "model": detail.get("model", ""),
            "file_count": file_count,
            "outputs_count": out_count,
            "is_current": bool(mgr.session and mgr.session.session_id == sid),
        })
    return {"sessions": results}


@app.get("/api/sessions/{session_id}")
async def get_session_detail(session_id: str) -> dict[str, Any]:
    from apodex.session_state import load_session_state

    mgr = get_manager()
    state = load_session_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    outputs_dir = Path(mgr.cwd) / ".apodex" / "runs" / session_id / "outputs"
    outputs = []
    if outputs_dir.exists():
        for p in outputs_dir.rglob("*"):
            if p.is_file():
                outputs.append({
                    "name": str(p.relative_to(outputs_dir)),
                    "size": p.stat().st_size,
                    "path": str(p),
                })
    return {
        "session_id": session_id,
        "state": state,
        "outputs": outputs,
    }


@app.post("/api/sessions/{session_id}/resume")
async def resume_saved_session(session_id: str) -> dict[str, Any]:
    from apodex.session_state import load_session_state

    mgr = get_manager()
    if mgr.is_running:
        raise HTTPException(status_code=400, detail="Cannot resume while agent is running.")
    state = load_session_state(session_id)
    if not state:
        raise HTTPException(status_code=404, detail="Session not found")
    if not mgr.session:
        mgr._init_session(state.get("mode", "react"), session_id=session_id)
    assert mgr.session is not None
    mgr.session.switch_session(state, fallback_id=session_id)
    mgr.mode = mgr.session.mode
    await mgr.broadcaster.emit("session_resumed", {"session_id": session_id, "mode": mgr.mode})
    return {"status": "ok", "session_id": session_id, "mode": mgr.mode}


@app.get("/api/all_files")
async def get_all_work_files() -> dict[str, Any]:
    from apodex.session_state import list_saved_sessions, load_session_state

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
        out_dir = Path(mgr.cwd) / ".apodex" / "runs" / sid / "outputs"
        if out_dir.exists():
            for p in out_dir.rglob("*"):
                if p.is_file():
                    all_outputs.append({
                        "session_id": sid,
                        "name": str(p.relative_to(out_dir)),
                        "size": p.stat().st_size,
                        "path": str(p),
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
