# agent/ide_bridge.py
from __future__ import annotations

"""IDE Bridge (MK3.5)

Local FastAPI server that IDE adapters (VS Code extension, etc.) talk to.

Security:
- Binds to 127.0.0.1 by default.
- Requires a Bearer token stored at jarvis-agent/workspace/ide/token.json

High-level flow:
1) IDE creates a session with the workspace root path.
2) IDE streams context (active file, selection, dirty buffers, diagnostics).
3) IDE asks for a "request" in natural language.
4) Jarvis proposes a sandboxed patch + runs checks.
5) IDE shows diff; user optionally applies with typed confirmation.
"""

import os
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from .ide_store import get_or_create_token, create_session, load_session, save_session
from .ide_pipeline import propose_patch, apply_pending_patch, discard_pending_patch


# -------------------------
# Auth
# -------------------------


def _require_auth(authorization: Optional[str]) -> None:
    token = get_or_create_token()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization: Bearer <token>")
    provided = authorization.split(" ", 1)[1].strip()
    if not secrets.compare_digest(provided, token):
        raise HTTPException(status_code=401, detail="Invalid token")


# -------------------------
# API models
# -------------------------


class SessionStartIn(BaseModel):
    workspace_root: str = Field(..., description="Absolute path to the project workspace")
    client: str = Field("vscode", description="Client identifier")
    preferences: Dict[str, Any] = Field(default_factory=dict)


class SessionStartOut(BaseModel):
    session_id: str


class ContextUpdateIn(BaseModel):
    active_file: Optional[str] = None
    selection: Optional[Dict[str, Any]] = None
    buffers: Dict[str, Dict[str, Any]] = Field(default_factory=dict)


class DiagnosticsUpdateIn(BaseModel):
    diagnostics: List[Dict[str, Any]] = Field(default_factory=list)


class RequestIn(BaseModel):
    prompt: str
    options: Dict[str, Any] = Field(default_factory=dict)


class RequestOut(BaseModel):
    job_id: str


class ApplyIn(BaseModel):
    confirm: str


# -------------------------
# Server
# -------------------------


app = FastAPI(title="Jarvis IDE Bridge", version="0.1")

# Useful for VS Code webviews / local dev tools
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_executor = ThreadPoolExecutor(max_workers=int(os.environ.get("JARVIS_IDE_WORKERS", "1")))
_jobs: Dict[str, Dict[str, Any]] = {}
_jobs_lock = threading.Lock()


def _new_job_id() -> str:
    return secrets.token_hex(8)


def _set_job(job_id: str, patch: Dict[str, Any]) -> None:
    with _jobs_lock:
        _jobs[job_id] = {**_jobs.get(job_id, {}), **patch}


@app.get("/health")
def health() -> Dict[str, Any]:
    # Note: do NOT return token here.
    return {"ok": True}


@app.get("/v1/token_hint")
def token_hint() -> Dict[str, Any]:
    """Returns where the token is stored (never the token itself)."""
    # no auth needed
    from .ide_store import token_path

    return {"token_file": str(token_path())}


@app.post("/v1/session/start", response_model=SessionStartOut)
def session_start(payload: SessionStartIn, authorization: Optional[str] = Header(default=None)) -> SessionStartOut:
    _require_auth(authorization)
    s = create_session(payload.workspace_root, client=payload.client, preferences=payload.preferences)
    return SessionStartOut(session_id=s.id)


@app.get("/v1/session/{session_id}/status")
def session_status(session_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    s = load_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")
    return {
        "result": {
            "id": s.id,
            "workspace_root": s.workspace_root,
            "client": s.client,
            "created_at": s.created_at,
            "has_pending_patch": bool(s.pending_patch),
            "pending_patch": s.pending_patch,
        }
    }


@app.post("/v1/session/{session_id}/context")
def session_context_update(session_id: str, payload: ContextUpdateIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    s = load_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")

    # merge
    if payload.active_file is not None:
        s.context["active_file"] = payload.active_file
    if payload.selection is not None:
        s.context["selection"] = payload.selection
    if payload.buffers:
        buffers = s.context.get("buffers")
        if not isinstance(buffers, dict):
            buffers = {}
        for k, v in payload.buffers.items():
            if isinstance(k, str) and isinstance(v, dict):
                buffers[k] = v
        s.context["buffers"] = buffers

    save_session(s)
    return {"result": {"updated": True}}


@app.post("/v1/session/{session_id}/diagnostics")
def session_diagnostics_update(session_id: str, payload: DiagnosticsUpdateIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    s = load_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")
    s.diagnostics = payload.diagnostics or []
    save_session(s)
    return {"result": {"updated": True, "count": len(s.diagnostics)}}


@app.post("/v1/session/{session_id}/request", response_model=RequestOut)
def session_request(session_id: str, payload: RequestIn, authorization: Optional[str] = Header(default=None)) -> RequestOut:
    _require_auth(authorization)

    s = load_session(session_id)
    if not s:
        raise HTTPException(status_code=404, detail="Unknown session")

    job_id = _new_job_id()
    _set_job(job_id, {"status": "queued"})

    def _work() -> None:
        _set_job(job_id, {"status": "running"})
        try:
            res = propose_patch(session_id=session_id, user_prompt=payload.prompt, options=payload.options)
            _set_job(job_id, {"status": "done", "result": res})
        except Exception as e:
            _set_job(job_id, {"status": "error", "error": str(e)})

    _executor.submit(_work)
    return RequestOut(job_id=job_id)


@app.get("/v1/job/{job_id}")
def job_status(job_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    with _jobs_lock:
        j = _jobs.get(job_id)
    if not j:
        raise HTTPException(status_code=404, detail="Unknown job")
    return {"result": j}


@app.post("/v1/session/{session_id}/apply")
def session_apply(session_id: str, payload: ApplyIn, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    res = apply_pending_patch(session_id, confirm=payload.confirm)
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res)
    return res


@app.post("/v1/session/{session_id}/discard")
def session_discard(session_id: str, authorization: Optional[str] = Header(default=None)) -> Dict[str, Any]:
    _require_auth(authorization)
    res = discard_pending_patch(session_id)
    if res.get("error"):
        raise HTTPException(status_code=400, detail=res)
    return res


def main() -> None:
    """Run the IDE Bridge server.

    Usage:
      conda activate jarvis-agent
      python -m agent.ide_bridge

    Environment:
      JARVIS_IDE_HOST (default 127.0.0.1)
      JARVIS_IDE_PORT (default 8765)
      JARVIS_IDE_WORKERS (default 1)

    Token:
      Stored at jarvis-agent/workspace/ide/token.json
    """

    import uvicorn

    host = os.environ.get("JARVIS_IDE_HOST", "127.0.0.1")
    port = int(os.environ.get("JARVIS_IDE_PORT", "8765"))

    token = get_or_create_token()
    print("[IDE Bridge] Token stored in jarvis-agent/workspace/ide/token.json")
    print(f"[IDE Bridge] Listening on http://{host}:{port}")
    print("[IDE Bridge] Clients must send Authorization: Bearer <token>")

    uvicorn.run("agent.ide_bridge:app", host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
