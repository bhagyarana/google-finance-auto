from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
from typing import Any

# Windows: force ProactorEventLoop so Playwright can spawn the browser subprocess.
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).parent))

from automation import run_automation, AUTH_STATE, RECORDINGS_DIR
from excel_parser import parse_excel, ParseError
from fifo import apply_fifo

app = FastAPI(title="Google Finance Bulk Upload", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_PATH = Path(__file__).parent.parent / "frontend" / "index.html"
UPLOAD_DIR    = Path(__file__).parent.parent / "uploads"
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
RECORDINGS_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/recordings", StaticFiles(directory=str(RECORDINGS_DIR)), name="recordings")

_tasks: dict[str, dict[str, Any]] = {}


@app.get("/", response_class=HTMLResponse)
async def serve_frontend():
    if FRONTEND_PATH.exists():
        return HTMLResponse(FRONTEND_PATH.read_text(encoding="utf-8"))
    return HTMLResponse("<h1>Frontend not found</h1><p>Expected: frontend/index.html</p>", status_code=404)


@app.get("/status")
async def status():
    return {
        "auth_saved": AUTH_STATE.exists(),
        "auth_path":  str(AUTH_STATE),
    }


@app.delete("/auth")
async def clear_auth():
    """Delete saved browser state so the next run triggers a fresh manual login."""
    if AUTH_STATE.exists():
        AUTH_STATE.unlink()
        return {"message": "Auth state cleared. Next automation run will require manual login."}
    return {"message": "No saved auth state found."}


@app.post("/upload")
async def upload_excel(
    file: UploadFile = File(...),
    skip_rows: int = Form(0),
):
    if not file.filename.endswith((".xlsx", ".xls")):
        raise HTTPException(400, "Only .xlsx / .xls files are accepted.")

    suffix = Path(file.filename).suffix
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix, dir=UPLOAD_DIR)
    try:
        content = await file.read()
        tmp.write(content)
        tmp.close()

        raw_trades, parse_warnings = parse_excel(tmp.name, skip_rows=skip_rows)
        fifo_result = apply_fifo(raw_trades)
        all_warnings = parse_warnings + fifo_result.warnings

        task_id = str(uuid.uuid4())
        _tasks[task_id] = {
            "trades":    fifo_result.net_trades,
            "file_path": tmp.name,
        }

        return {
            "task_id":      task_id,
            "total_raw":    len(raw_trades),
            "raw_trades":   [t.to_dict() for t in raw_trades],
            "total":        len(fifo_result.net_trades),
            "trades":       [t.to_dict() for t in fifo_result.net_trades],
            "fifo_summary": [s.to_dict() for s in fifo_result.summaries],
            "warnings":     all_warnings,
        }

    except ParseError as e:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(422, str(e))

    except Exception as e:
        tmp.close()
        Path(tmp.name).unlink(missing_ok=True)
        raise HTTPException(500, f"Unexpected error: {e}")


@app.post("/run")
async def run(
    task_id: str = Form(...),
    portfolio_name: str = Form("My Portfolio"),
    dry_run: bool = Form(False),
    headless: bool = Form(False),
    create_if_missing: bool = Form(True),
    record: bool = Form(False),
):
    if task_id not in _tasks:
        raise HTTPException(404, f"Unknown task_id '{task_id}'. Please upload a file first.")

    _tasks[task_id].update({
        "portfolio_name":     portfolio_name,
        "dry_run":            dry_run,
        "headless":           headless,
        "create_if_missing":  create_if_missing,
        "record":             record,
        "ready":              True,
    })
    return {"task_id": task_id, "status": "queued"}


@app.get("/recordings")
async def list_recordings():
    if not RECORDINGS_DIR.exists():
        return {"sessions": []}
    sessions = []
    for session_dir in sorted(RECORDINGS_DIR.iterdir(), reverse=True):
        if not session_dir.is_dir():
            continue
        files = []
        for f in sorted(session_dir.rglob("*")):
            if f.is_file():
                files.append({
                    "path": f"recordings/{f.relative_to(RECORDINGS_DIR).as_posix()}",
                    "name": f.name,
                    "size_kb": round(f.stat().st_size / 1024, 1),
                })
        sessions.append({"session": session_dir.name, "files": files})
    return {"sessions": sessions}


@app.get("/progress/{task_id}")
async def progress(task_id: str):
    if task_id not in _tasks:
        raise HTTPException(404, f"Unknown task_id '{task_id}'.")

    task = _tasks[task_id]
    if not task.get("ready"):
        raise HTTPException(400, "Task is not ready. Call POST /run first.")

    trades            = task["trades"]
    portfolio_name    = task.get("portfolio_name", "My Portfolio")
    dry_run           = task.get("dry_run", False)
    headless          = task.get("headless", False)
    create_if_missing = task.get("create_if_missing", True)
    record            = task.get("record", False)

    async def event_generator():
        try:
            async for event in run_automation(
                trades=trades,
                portfolio_name=portfolio_name,
                dry_run=dry_run,
                headless=headless,
                create_if_missing=create_if_missing,
                record=record,
            ):
                yield {"data": json.dumps(event.to_dict())}
        except Exception as e:
            yield {"data": json.dumps({"kind": "error", "message": str(e), "row": None, "detail": ""})}
        finally:
            fp = _tasks.get(task_id, {}).get("file_path")
            if fp:
                Path(fp).unlink(missing_ok=True)
            _tasks.pop(task_id, None)

    return EventSourceResponse(event_generator())
