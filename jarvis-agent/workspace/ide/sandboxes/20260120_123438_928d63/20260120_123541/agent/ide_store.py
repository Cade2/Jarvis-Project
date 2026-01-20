# agent/ide_store.py
from __future__ import annotations

"""Persistent storage for the IDE Bridge.

We store state under Jarvis' own workspace folder:
  jarvis-agent/workspace/ide/

That keeps the IDE agent "workspace-agnostic" (it can work on any project)
while keeping all Jarvis artifacts inside the Jarvis repo.
"""

from dataclasses import dataclass, asdict
from datetime import datetime
import json
import os
import secrets
from pathlib import Path
from typing import Any, Dict, Optional


def _repo_root() -> Path:
    # agent/ide_store.py -> agent/ -> repo root
    return Path(__file__).resolve().parent.parent


def ide_root() -> Path:
    return _repo_root() / "workspace" / "ide"


def sessions_dir() -> Path:
    return ide_root() / "sessions"


def sandboxes_dir() -> Path:
    return ide_root() / "sandboxes"


def backups_dir() -> Path:
    return ide_root() / "backups"


def logs_dir() -> Path:
    return ide_root() / "runs"


def token_path() -> Path:
    return ide_root() / "token.json"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def ensure_layout() -> None:
    ide_root().mkdir(parents=True, exist_ok=True)
    sessions_dir().mkdir(parents=True, exist_ok=True)
    sandboxes_dir().mkdir(parents=True, exist_ok=True)
    backups_dir().mkdir(parents=True, exist_ok=True)
    logs_dir().mkdir(parents=True, exist_ok=True)


def get_or_create_token() -> str:
    """Return an auth token for the IDE bridge.

    Stored in workspace/ide/token.json. This token is required for all API calls.
    """
    ensure_layout()
    p = token_path()
    if p.exists():
        try:
            obj = json.loads(p.read_text(encoding="utf-8"))
            t = (obj.get("token") or "").strip()
            if t:
                return t
        except Exception:
            pass

    token = secrets.token_urlsafe(32)
    p.write_text(json.dumps({"token": token, "created_at": _now_iso()}, indent=2), encoding="utf-8")
    return token


def new_session_id() -> str:
    # short and filesystem-friendly
    return datetime.now().strftime("%Y%m%d_%H%M%S") + "_" + secrets.token_hex(3)


@dataclass
class IDESession:
    id: str
    created_at: str
    client: str
    workspace_root: str
    preferences: Dict[str, Any]
    context: Dict[str, Any]
    diagnostics: list
    pending_patch: Optional[Dict[str, Any]]
    history: list


def create_session(workspace_root: str, client: str = "unknown", preferences: Optional[Dict[str, Any]] = None) -> IDESession:
    ensure_layout()
    sid = new_session_id()
    s = IDESession(
        id=sid,
        created_at=_now_iso(),
        client=client or "unknown",
        workspace_root=os.path.abspath(workspace_root),
        preferences=preferences or {},
        context={
            "active_file": None,
            "selection": None,
            # buffers: {"relative/or/abs/path": {"content": str, "dirty": bool, "updated_at": iso}}
            "buffers": {},
        },
        diagnostics=[],
        pending_patch=None,
        history=[],
    )
    save_session(s)
    return s


def session_path(session_id: str) -> Path:
    return sessions_dir() / f"{session_id}.json"


def load_session(session_id: str) -> Optional[IDESession]:
    p = session_path(session_id)
    if not p.exists():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
        return IDESession(**obj)
    except Exception:
        return None


def save_session(session: IDESession) -> None:
    ensure_layout()
    p = session_path(session.id)
    p.write_text(json.dumps(asdict(session), indent=2), encoding="utf-8", errors="replace")


def update_session(session_id: str, patch: Dict[str, Any]) -> Optional[IDESession]:
    s = load_session(session_id)
    if not s:
        return None
    # shallow merge
    for k, v in patch.items():
        setattr(s, k, v)
    save_session(s)
    return s
