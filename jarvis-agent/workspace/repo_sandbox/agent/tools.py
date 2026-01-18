from typing import Dict, Any
import subprocess
import sys
import json
from pathlib import Path
from .policy import Policy
from .runner_manager import ensure_runner_started
from .runner_client import RunnerClient
from .elevation import relaunch_runner_elevated
from .safety import Tool, RiskLevel

import os
import re
from datetime import datetime


from collections import Counter
from typing import List, Dict, Any, Tuple

import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, Any



from .safety import Tool, RiskLevel

from .devtools import (
    dev_status,
    dev_sandbox_reset,
    dev_propose_patch,
    dev_discard_patch,
    dev_apply_patch,
)

_runner = RunnerClient()
_policy = Policy.load()


# ---- Reminder storage helpers ----

REMINDERS_FILE = Path("reminders.json")

from .safety import Tool, RiskLevel, get_audit_path

def _load_reminders():
    if REMINDERS_FILE.exists():
        try:
            with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except json.JSONDecodeError:
            # If the file is corrupted, just start fresh
            return []
    return []


def _save_reminders(reminders) -> None:
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2)


# ---- Tool implementations ----

def create_reminder(params: Dict[str, Any]):
    """
    v0: print the reminder AND save it locally to reminders.json.
    """
    text = params.get("text", "No text provided")
    when = params.get("when", "unspecified time")
    print(f"[REMINDER] {text} @ {when}")

    reminders = _load_reminders()
    reminders.append({"text": text, "when": when})
    _save_reminders(reminders)


def list_reminders(params: Dict[str, Any] = None):
    """
    Print all saved reminders from reminders.json.
    """
    reminders = _load_reminders()
    if not reminders:
        print("[REMINDERS] You don't have any saved reminders yet.")
        return

    print("[REMINDERS]")
    for idx, r in enumerate(reminders, start=1):
        text = r.get("text", "<no text>")
        when = r.get("when", "unspecified time")
        print(f"{idx}. {text} @ {when}")


def delete_reminder(params: Dict[str, Any]):
    """
    Delete a single reminder by 1-based index.
    """
    index = params.get("index")
    if index is None:
        print("[REMINDERS] No reminder number provided.")
        return

    try:
        idx = int(index)
    except ValueError:
        print("[REMINDERS] Reminder number must be a whole number.")
        return

    reminders = _load_reminders()
    if idx < 1 or idx > len(reminders):
        print(f"[REMINDERS] There is no reminder #{idx}. You currently have {len(reminders)} reminder(s).")
        return

    removed = reminders.pop(idx - 1)
    _save_reminders(reminders)

    text = removed.get("text", "<no text>")
    when = removed.get("when", "unspecified time")
    print(f"[REMINDERS] Deleted #{idx}: {text} @ {when}")


def clear_reminders(params: Dict[str, Any]):
    """
    Remove ALL reminders after a confirmation prompt.
    """
    reminders = _load_reminders()
    if not reminders:
        print("[REMINDERS] There are no reminders to clear.")
        return

    confirm = input("Jarvis: Are you sure you want to delete ALL reminders? (y/n): ").strip().lower()
    if confirm not in ("y", "yes"):
        print("[REMINDERS] Okay, I won't delete anything.")
        return

    _save_reminders([])
    print("[REMINDERS] All reminders have been deleted.")


def show_activity(params: Dict[str, Any]):
    """
    Show the most recent actions from the current session audit log file.
    """
    params = params or {}
    limit = int(params.get("limit", 10))

    audit_file = get_audit_path()

    if not audit_file.exists():
        print("[ACTIVITY] No audit file found for this session yet.")
        return

    try:
        with open(audit_file, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[ACTIVITY] Failed to read audit file: {e}")
        return

    # Optional: skip the header line starting with "#"
    lines = [ln for ln in lines if not ln.strip().startswith("#")]

    if not lines:
        print("[ACTIVITY] Audit log is empty for this session.")
        return

    print(f"[ACTIVITY] Recent actions (from {audit_file.name}):")
    for idx, line in enumerate(lines[-limit:], start=1):
        print(f"{idx}. {line.strip()}")



# ---- MK3.2: Read-only introspection (logs + code) ----

def _repo_root() -> Path:
    # agent/tools.py -> agent/ -> jarvis-agent/
    return Path(__file__).resolve().parent.parent

def _safe_under_repo(p: Path) -> Path:
    """
    Ensure p resolves inside repo root (prevents reading arbitrary files).
    """
    root = _repo_root()
    resolved = (root / p).resolve() if not p.is_absolute() else p.resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Path '{p}' is outside the repo root.")
    return resolved


def logs_list(params: Dict[str, Any] = None):
    """
    List recent audit logs in ./logs (newest first).
    """
    params = params or {}
    limit = int(params.get("limit", 10))

    logs_dir = _repo_root() / "logs"
    if not logs_dir.exists():
        return {"result": {"logs": [], "note": "No logs/ folder found yet."}}

    files = sorted(
        logs_dir.glob("audit_*.log"),
        key=lambda p: p.stat().st_mtime,
        reverse=True
    )[:limit]

    out = []
    for f in files:
        st = f.stat()
        out.append({
            "name": f.name,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            "size_bytes": st.st_size,
        })

    return {"result": {"logs": out}}

def logs_tail(params: Dict[str, Any]):
    """
    Tail a specific log file (by name or path) and return last N lines.
    """
    params = params or {}
    lines = int(params.get("lines", 50))
    file_ = params.get("file") or params.get("path")

    if not file_:
        return {"error": "Missing 'file' (e.g. 'audit_20260113_014831.log' or 'logs/audit_....log')."}

    # allow: "audit_....log" or "logs/audit_....log"
    p = Path(file_)
    if p.parent == Path("."):
        p = Path("logs") / p

    try:
        p = _safe_under_repo(p)
    except Exception as e:
        return {"error": str(e)}

    if not p.exists():
        return {"error": f"Log file not found: {p}"}

    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"error": f"Failed to read log file: {e}"}

    # remove header lines
    body = [ln.rstrip("\n") for ln in all_lines if not ln.strip().startswith("#")]
    tail = body[-lines:] if lines > 0 else body

    return {"result": {"file": str(p.relative_to(_repo_root())), "lines": tail}}

def logs_last(params: Dict[str, Any] = None):
    """
    Tail the current session audit log (uses get_audit_path()).
    """
    params = params or {}
    lines = int(params.get("lines", 50))
    audit_file = get_audit_path()

    if not audit_file.exists():
        return {"error": "No audit file found for this session yet."}

    try:
        with open(audit_file, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
    except Exception as e:
        return {"error": f"Failed to read audit file: {e}"}

    body = [ln.rstrip("\n") for ln in all_lines if not ln.strip().startswith("#")]
    tail = body[-lines:] if lines > 0 else body

    return {"result": {"file": str(audit_file), "lines": tail}}

def _parse_audit_tail(tail_lines: List[str]) -> Tuple[Dict[str, int], List[Dict[str, Any]], List[str]]:
    """
    Parse lines like:
    2026-01-13T10:25:11.914692 | logs.summarize_tail | {'lines': 120} | success
    """
    counts = {
        "traceback": 0,
        "error": 0,
        "exception": 0,
        "policy_blocks": 0,
        "tool_calls": 0,
    }

    tool_counter = Counter()
    notes: List[str] = []

    for raw in tail_lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        # Some lines might not match perfectly; be defensive
        parts = line.split(" | ")
        if len(parts) >= 4:
            tool_name = parts[1].strip()
            outcome = parts[-1].strip().lower()

            counts["tool_calls"] += 1
            tool_counter[tool_name] += 1

            if "traceback" in line.lower():
                counts["traceback"] += 1
            if outcome.startswith("error"):
                counts["error"] += 1
                notes.append(f"Error: {tool_name}")
            if "exception" in outcome:
                counts["exception"] += 1
                notes.append(f"Exception: {tool_name}")
            if "policy" in outcome:
                counts["policy_blocks"] += 1
                notes.append(f"Policy block: {tool_name}")
            if "cancel" in outcome:
                notes.append(f"Cancelled: {tool_name}")
        else:
            # Not in the standard format; still detect tracebacks/errors if present
            if "traceback" in line.lower():
                counts["traceback"] += 1
            if "error" in line.lower():
                counts["error"] += 1

    top_tools = [
        {"tool": name, "count": cnt}
        for name, cnt in tool_counter.most_common(8)
    ]

    # Deduplicate notes, keep order
    seen = set()
    deduped_notes = []
    for n in notes:
        if n not in seen:
            seen.add(n)
            deduped_notes.append(n)

    return counts, top_tools, deduped_notes


def logs_summarize_tail(params: Dict[str, Any]):
    """
    Deterministic summary of last N lines (pattern spotting).
    Read-only and safe (no LLM calls yet).
    """
    params = params or {}
    lines_n = int(params.get("lines", 80))
    file_ = params.get("file") or params.get("path")  # optional

    # use logs_last if no file specified
    if not file_:
        tail_res = logs_last({"lines": lines_n})
    else:
        tail_res = logs_tail({"lines": lines_n, "file": file_})

    if "error" in tail_res:
        return tail_res

    tail_lines = tail_res["result"]["lines"]
    counts, top_tools, notes = _parse_audit_tail(tail_lines)

    return {
        "result": {
            "file": tail_res["result"]["file"],
            "summary": {
                "counts": counts,
                "top_planned_tools": top_tools,
                "notes": notes,
            },
            "tail_preview": tail_lines[-15:] if len(tail_lines) > 15 else tail_lines
        }
    }




def code_read_file(params: Dict[str, Any]):
    """
    Read a file inside the repo (bounded).
    Params: path (required), max_lines (default 200), start_line (default 1)
    """
    params = params or {}
    path = params.get("path")
    if not path:
        return {"error": "Missing 'path' (e.g. 'agent/core.py')."}

    max_lines = int(params.get("max_lines", 200))
    start_line = int(params.get("start_line", 1))

    try:
        p = _safe_under_repo(Path(path))
    except Exception as e:
        return {"error": str(e)}

    if not p.exists() or not p.is_file():
        return {"error": f"File not found: {p}"}

    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as e:
        return {"error": f"Failed to read file: {e}"}

    # 1-based start_line
    start_idx = max(start_line - 1, 0)
    chunk = lines[start_idx:start_idx + max_lines]

    numbered = []
    for i, ln in enumerate(chunk, start=start_idx + 1):
        numbered.append(f"{i:>4} | {ln.rstrip()}")

    return {"result": {"path": str(p.relative_to(_repo_root())), "lines": numbered}}

def code_search(params: Dict[str, Any]):
    """
    Search for a query in a file or directory inside the repo (bounded).
    Params:
      query (required)
      path (default 'agent')
      max_files (default 50)
      max_matches (default 50)
      case_sensitive (default false)
    """
    params = params or {}
    query = params.get("query")
    if not query:
        return {"error": "Missing 'query'."}

    base = params.get("path", "agent")
    max_files = int(params.get("max_files", 50))
    max_matches = int(params.get("max_matches", 50))
    case_sensitive = bool(params.get("case_sensitive", False))

    try:
        base_path = _safe_under_repo(Path(base))
    except Exception as e:
        return {"error": str(e)}

    if not base_path.exists():
        return {"error": f"Path not found: {base_path}"}

    matches = []
    files_scanned = 0

    def _scan_file(fp: Path):
        nonlocal files_scanned, matches
        if files_scanned >= max_files or len(matches) >= max_matches:
            return
        files_scanned += 1

        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except Exception:
            return

        hay = text if case_sensitive else text.lower()
        needle = query if case_sensitive else query.lower()

        if needle not in hay:
            return

        # capture line-level matches
        for idx, line in enumerate(text.splitlines(), start=1):
            hline = line if case_sensitive else line.lower()
            if needle in hline:
                matches.append({
                    "file": str(fp.relative_to(_repo_root())),
                    "line": idx,
                    "text": line.strip()[:300],
                })
                if len(matches) >= max_matches:
                    return

    if base_path.is_file():
        _scan_file(base_path)
    else:
        for fp in base_path.rglob("*.py"):
            _scan_file(fp)
            if files_scanned >= max_files or len(matches) >= max_matches:
                break

    return {
        "result": {
            "query": query,
            "path": str(base_path.relative_to(_repo_root())),
            "files_scanned": files_scanned,
            "matches": matches,
        }
    }


# ---- MK3.3: Guarded filesystem tools (workspace sandbox) ----

def _workspace_root() -> Path:
    return _repo_root() / "workspace"

def _safe_under_workspace(p: Path) -> Path:
    """
    Ensure p resolves inside workspace/ (prevents reading arbitrary files).
    """
    root = _workspace_root().resolve()
    resolved = (root / p).resolve() if not p.is_absolute() else p.resolve()
    if root not in resolved.parents and resolved != root:
        raise ValueError(f"Path '{p}' is outside workspace/.")
    return resolved

def fs_list_dir(params: Dict[str, Any] = None):
    """
    List files/folders inside workspace (default: workspace root).
    Params: path (optional), show_hidden (optional bool)
    """
    params = params or {}
    rel = params.get("path", ".")
    show_hidden = bool(params.get("show_hidden", False))

    try:
        base = _safe_under_workspace(Path(rel))
    except Exception as e:
        return {"error": str(e)}

    if not base.exists():
        return {"error": f"Path not found: {base}"}
    if not base.is_dir():
        return {"error": f"Not a directory: {base}"}

    items = []
    for child in sorted(base.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
        name = child.name
        if not show_hidden and name.startswith("."):
            continue
        st = child.stat()
        items.append({
            "name": name,
            "type": "dir" if child.is_dir() else "file",
            "size_bytes": st.st_size if child.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        })

    return {"result": {"path": str(base.relative_to(_repo_root())), "items": items}}

def fs_stat(params: Dict[str, Any]):
    """
    Stat a file/folder inside workspace.
    Params: path (required)
    """
    params = params or {}
    rel = params.get("path")
    if not rel:
        return {"error": "Missing 'path'."}

    try:
        p = _safe_under_workspace(Path(rel))
    except Exception as e:
        return {"error": str(e)}

    if not p.exists():
        return {"error": f"Path not found: {p}"}

    st = p.stat()
    return {
        "result": {
            "path": str(p.relative_to(_repo_root())),
            "type": "dir" if p.is_dir() else "file",
            "size_bytes": st.st_size if p.is_file() else 0,
            "modified": datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
        }
    }

def fs_mkdir(params: Dict[str, Any]):
    """
    Create a folder inside workspace.
    Params: path (required)
    """
    params = params or {}
    rel = params.get("path")
    if not rel:
        return {"error": "Missing 'path'."}

    try:
        p = _safe_under_workspace(Path(rel))
    except Exception as e:
        return {"error": str(e)}

    p.mkdir(parents=True, exist_ok=True)
    return {"result": {"created": True, "path": str(p.relative_to(_repo_root()))}}

def fs_copy(params: Dict[str, Any]):
    """
    Copy a file (or folder) inside workspace.
    Params: src (required), dst (required)
    """
    params = params or {}
    src = params.get("src")
    dst = params.get("dst")
    if not src or not dst:
        return {"error": "Missing 'src' or 'dst'."}

    try:
        srcp = _safe_under_workspace(Path(src))
        dstp = _safe_under_workspace(Path(dst))
    except Exception as e:
        return {"error": str(e)}

    if not srcp.exists():
        return {"error": f"Source not found: {srcp}"}

    if srcp.is_dir():
        if dstp.exists():
            return {"error": "Destination already exists for directory copy."}
        shutil.copytree(srcp, dstp)
    else:
        dstp.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(srcp, dstp)

    return {"result": {"copied": True, "src": str(srcp.relative_to(_repo_root())), "dst": str(dstp.relative_to(_repo_root()))}}

def fs_move(params: Dict[str, Any]):
    """
    Move/rename a file/folder inside workspace.
    Params: src (required), dst (required)
    """
    params = params or {}
    src = params.get("src")
    dst = params.get("dst")
    if not src or not dst:
        return {"error": "Missing 'src' or 'dst'."}

    try:
        srcp = _safe_under_workspace(Path(src))
        dstp = _safe_under_workspace(Path(dst))
    except Exception as e:
        return {"error": str(e)}

    if not srcp.exists():
        return {"error": f"Source not found: {srcp}"}

    dstp.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(srcp), str(dstp))

    return {"result": {"moved": True, "src": str(srcp.relative_to(_repo_root())), "dst": str(dstp.relative_to(_repo_root()))}}





def open_application(params: Dict[str, Any]):
    """
    Backwards-compatible wrapper that calls the MK2 runner.
    """
    app_name = params.get("app_name")
    if not app_name:
        print("No app_name provided.")
        return {"error": "No app_name provided"}

    ensure_runner_started()
    result = _runner.run_tool("apps.open", {"name": app_name})
    print(f"[OPEN APPLICATION] {app_name} -> {result}")
    return result


def close_application(params: Dict[str, Any]):
    """
    Backwards-compatible wrapper that calls the MK2 runner.
    """
    app_name = params.get("app_name")
    if not app_name:
        print("No app_name provided.")
        return {"error": "No app_name provided"}

    ensure_runner_started()
    result = _runner.run_tool("apps.close", {"name": app_name})
    print(f"[CLOSE APPLICATION] {app_name} -> {result}")
    return result

# ---- Tool registry ----

TOOLS: Dict[str, Tool] = {
    "create_reminder": Tool(
        name="create_reminder",
        description="Create a reminder for the user at a specific time.",
        risk=RiskLevel.LOW,
        func=create_reminder,
    ),
    "open_application": Tool(
        name="open_application",
        description="Open a desktop application by name.",
        risk=RiskLevel.MEDIUM,
        func=open_application,
    ),
    "close_application": Tool(
        name="close_application",
        description="Close a known application by name (currently very limited).",
        risk=RiskLevel.HIGH,
        func=close_application,
    ),
    "list_reminders": Tool(
        name="list_reminders",
        description="Show all reminders saved locally on this device.",
        risk=RiskLevel.LOW,
        func=list_reminders,
    ),
    "show_activity": Tool(
        name="show_activity",
        description="Show recent actions from the local audit log.",
        risk=RiskLevel.LOW,
        func=show_activity,
    ),
    "delete_reminder": Tool(
        name="delete_reminder",
        description="Delete a single reminder by its number in the list.",
        risk=RiskLevel.LOW,
        func=delete_reminder,
    ),
    "clear_reminders": Tool(
        name="clear_reminders",
        description="Delete all saved reminders after user confirmation.",
        risk=RiskLevel.MEDIUM,  # a bit more dangerous
        func=clear_reminders,
    ),
        "logs.list": Tool(
        name="logs.list",
        description="List recent audit logs (read-only).",
        risk=RiskLevel.READ_ONLY,
        func=logs_list,
    ),
    "logs.tail": Tool(
        name="logs.tail",
        description="Show last N lines of a log file (read-only).",
        risk=RiskLevel.READ_ONLY,
        func=logs_tail,
    ),
    "logs.last": Tool(
        name="logs.last",
        description="Show last N lines of the current session log (read-only).",
        risk=RiskLevel.READ_ONLY,
        func=logs_last,
    ),
    "logs.summarize_tail": Tool(
        name="logs.summarize_tail",
        description="Summarize last N lines of a log file (deterministic, read-only).",
        risk=RiskLevel.READ_ONLY,
        func=logs_summarize_tail,
    ),
        "code.read_file": Tool(
        name="code.read_file",
        description="Read a repo file safely (bounded).",
        risk=RiskLevel.READ_ONLY,
        func=code_read_file,
    ),
    "code.search": Tool(
        name="code.search",
        description="Search query in repo code safely (bounded).",
        risk=RiskLevel.READ_ONLY,
        func=code_search,
    ),


    
}

# ---- Runner-backed tools (MK2) ----
from .runner_client import RunnerClient
from .runner_manager import ensure_runner_started, restart_runner
from .policy import Policy

_policy = Policy.load()
_runner = RunnerClient()

def _runner_tool(tool_name: str):
    def _call(params: Dict[str, Any]):
        if not _policy.is_domain_allowed(tool_name):
            return {"error": f"Policy blocks tool '{tool_name}' (domain not allowed)."}
        ensure_runner_started()
        return _runner.run_tool(tool_name, params)
    return _call

TOOLS.update({
    "system.get_info": Tool(
        name="system.get_info",
        description="Read system info (OS, CPU, RAM, uptime).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("system.get_info"),
    ),
    "system.get_storage": Tool(
        name="system.get_storage",
        description="Read disk storage info (free/total).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("system.get_storage"),
    ),
    "apps.list_installed": Tool(
        name="apps.list_installed",
        description="List installed applications on this device.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("apps.list_installed"),
    ),
    "apps.open": Tool(
        name="apps.open",
        description="Open an application by name (best effort).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("apps.open"),
    ),
    "apps.close": Tool(
        name="apps.close",
        description="Force close an application by name (can lose work).",
        risk=RiskLevel.HIGH,
        func=_runner_tool("apps.close"),
    ),
    "settings.open": Tool(
    name="settings.open",
    description="Open a Windows Settings page (ms-settings deep link).",
    risk=RiskLevel.LOW,
    func=_runner_tool("settings.open"),
    ),
    "network.get_state": Tool(
    name="network.get_state",
    description="Read network state (Wi-Fi adapter + connection info).",
    risk=RiskLevel.READ_ONLY,
    func=_runner_tool("network.get_state"),
    ),
    "network.toggle_wifi": Tool(
        name="network.toggle_wifi",
        description="Enable/disable Wi-Fi (verified before/after).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("network.toggle_wifi"),
    ),
    "network.toggle_airplane_mode": Tool(
        name="network.toggle_airplane_mode",
        description="Airplane Mode (currently opens settings; direct toggle later).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("network.toggle_airplane_mode"),
    ),
    "display.get_state": Tool(
        name="display.get_state",
        description="Read display brightness state (best effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("display.get_state"),
    ),
    "display.set_brightness": Tool(
        name="display.set_brightness",
        description="Set screen brightness (0-100) with verification.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("display.set_brightness"),
    ),
    # Display (MK2 additions)
    "display.list_displays": Tool(
        name="display.list_displays",
        description="List connected displays and their current modes.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("display.list_displays"),
    ),
    "display.set_resolution": Tool(
        name="display.set_resolution",
        description="Set a display's resolution (width/height).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("display.set_resolution"),
    ),
    "display.set_refresh_rate": Tool(
        name="display.set_refresh_rate",
        description="Set a display's refresh rate (Hz).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("display.set_refresh_rate"),
    ),
    "display.set_orientation": Tool(
        name="display.set_orientation",
        description="Set a display's orientation (landscape/portrait/etc.).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("display.set_orientation"),
    ),
    "display.set_multiple_displays": Tool(
        name="display.set_multiple_displays",
        description="Switch multi-monitor mode (extend/duplicate/second screen only/PC screen only).",
        risk=RiskLevel.HIGH,
        func=_runner_tool("display.set_multiple_displays"),
    ),
    "display.set_scale": Tool(
        name="display.set_scale",
        description="Set scaling percentage (may require sign-out/in).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("display.set_scale"),
    ),
    "display.open_color_profile": Tool(
        name="display.open_color_profile",
        description="Open Color Management (color profiles).",
        risk=RiskLevel.LOW,
        func=_runner_tool("display.open_color_profile"),
    ),
    "display.open_hdr_settings": Tool(
        name="display.open_hdr_settings",
        description="Open HDR settings (fallback).",
        risk=RiskLevel.LOW,
        func=_runner_tool("display.open_hdr_settings"),
    ),
    "display.open_night_light": Tool(
        name="display.open_night_light",
        description="Open Night light settings (fallback).",
        risk=RiskLevel.LOW,
        func=_runner_tool("display.open_night_light"),
    ),

    "runner.is_elevated": Tool(
        name="runner.is_elevated",
        description="Check whether the runner is running as Administrator.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("runner.is_elevated"),
    ),
    "runner.relaunch_elevated": Tool(
        name="runner.relaunch_elevated",
        description="Relaunch the runner as Administrator (UAC prompt).",
        risk=RiskLevel.HIGH,
        func=relaunch_runner_elevated,
    ),
    
    "runner.restart": Tool(
    name="runner.restart",
    description="Restart the runner process (best effort). Useful to recover from a stuck/elevated runner.",
    risk=RiskLevel.HIGH,
    func=lambda params: (restart_runner() or {"result": {"restarted": True}}),
    ),

    "bluetooth.get_state": Tool(
        name="bluetooth.get_state",
        description="Read Bluetooth radio state (best effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("bluetooth.get_state"),
    ),
    "bluetooth.toggle": Tool(
        name="bluetooth.toggle",
        description="Enable/disable Bluetooth (verified before/after).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("bluetooth.toggle"),
    ),
    "bluetooth.list_paired": Tool(
        name="bluetooth.list_paired",
        description="List paired Bluetooth devices.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("bluetooth.list_paired"),
    ),
    "bluetooth.connect_paired": Tool(
        name="bluetooth.connect_paired",
        description="Best-effort pair/connect to a Bluetooth device by name (falls back to Settings if blocked).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("bluetooth.connect_paired"),
    ),
        "audio.get_state": Tool(
        name="audio.get_state",
        description="Read master volume + mute state.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("audio.get_state"),
    ),
    "audio.set_volume": Tool(
        name="audio.set_volume",
        description="Set master volume (0-100) with verification.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("audio.set_volume"),
    ),
    "audio.set_mute": Tool(
        name="audio.set_mute",
        description="Mute/unmute audio with verification.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("audio.set_mute"),
    ),
    "power.get_state": Tool(
        name="power.get_state",
        description="Read power plan state + battery info (best effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.get_state"),
    ),
    "power.list_schemes": Tool(
        name="power.list_schemes",
        description="List available power plans.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.list_schemes"),
    ),
    "power.set_scheme": Tool(
        name="power.set_scheme",
        description="Switch active power plan (balanced/high performance/power saver).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.set_scheme"),
    ),
    "power.get_mode": Tool(
        name="power.get_mode",
        description="Read Windows 11 Power Mode (plugged in / on battery).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.get_mode"),
    ),
    "power.set_mode": Tool(
        name="power.set_mode",
        description="Set Windows 11 Power Mode (best_power_efficiency/balanced/best_performance).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.set_mode"),
    ),
    "power.get_timeouts": Tool(
        name="power.get_timeouts",
        description="Get screen/sleep/hibernate timeouts (AC/DC).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.get_timeouts"),
    ),
    "power.set_sleep_timeout": Tool(
        name="power.set_sleep_timeout",
        description="Set sleep timeout in minutes (AC/DC/both).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.set_sleep_timeout"),
    ),
    "power.set_screen_timeout": Tool(
        name="power.set_screen_timeout",
        description="Set screen timeout in minutes (AC/DC/both).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.set_screen_timeout"),
    ),
    "power.set_hibernate_timeout": Tool(
        name="power.set_hibernate_timeout",
        description="Set hibernate timeout in minutes (AC/DC/both).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.set_hibernate_timeout"),
    ),

    "power.hibernate_status": Tool(
        name="power.hibernate_status",
        description="Check whether hibernate is enabled/available.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.hibernate_status"),
    ),
    "power.hibernate_on": Tool(
        name="power.hibernate_on",
        description="Enable hibernate.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.hibernate_on"),
    ),
    "power.hibernate_off": Tool(
        name="power.hibernate_off",
        description="Disable hibernate (removes hiberfil.sys).",
        risk=RiskLevel.HIGH,
        func=_runner_tool("power.hibernate_off"),
    ),

    "power.energy_saver_status": Tool(
        name="power.energy_saver_status",
        description="Read Energy Saver threshold (AC/DC).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.energy_saver_status"),
    ),
    "power.energy_saver_on": Tool(
        name="power.energy_saver_on",
        description="Turn Energy Saver on (sets threshold=100).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.energy_saver_on"),
    ),
    "power.energy_saver_off": Tool(
        name="power.energy_saver_off",
        description="Turn Energy Saver off (sets threshold=0).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.energy_saver_off"),
    ),
    "power.energy_saver_threshold": Tool(
        name="power.energy_saver_threshold",
        description="Set Energy Saver threshold percent (0-100) (AC/DC/both).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("power.energy_saver_threshold"),
    ),

    "power.battery_report": Tool(
        name="power.battery_report",
        description="Generate a battery report (HTML).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.battery_report"),
    ),
    "power.open_battery_usage": Tool(
        name="power.open_battery_usage",
        description="Open Windows Battery usage page.",
        risk=RiskLevel.LOW,
        func=_runner_tool("power.open_battery_usage"),
    ),
    "power.srum_report": Tool(
        name="power.srum_report",
        description="Generate a Windows SRUM report (per-app usage data). Returns path to CSV/XML.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("power.srum_report"),
    ),
    "storage.get_categories": Tool(
    name="storage.get_categories",
    description="Estimate storage usage by category (Downloads, Apps, Temp, Recycle Bin, etc.).",
    risk=RiskLevel.READ_ONLY,
    func=_runner_tool("storage.get_categories"),
    ),
    "storage.cleanup_recommendations": Tool(
        name="storage.cleanup_recommendations",
        description="Show cleanup recommendations (read-only) + largest Downloads files.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("storage.cleanup_recommendations"),
    ),
    "nearby.get_state": Tool(
        name="nearby.get_state",
        description="Read Nearby sharing mode + friendly discoverable name (best effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("nearby.get_state"),
    ),
    "nearby.set_mode": Tool(
        name="nearby.set_mode",
        description="Set Nearby sharing mode (off / my devices only / everyone nearby).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("nearby.set_mode"),
    ),
    "nearby.set_friendly_name": Tool(
        name="nearby.set_friendly_name",
        description="Rename Nearby sharing discoverable friendly name (admin required).",
        risk=RiskLevel.HIGH,
        func=_runner_tool("nearby.set_friendly_name"),
    ),
    "multitasking.get_state": Tool(
        name="multitasking.get_state",
        description="Get multitasking settings (snap windows, title bar shake, Alt+Tab tabs).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("multitasking.get_state"),
    ),
    "multitasking.set_snap_windows": Tool(
        name="multitasking.set_snap_windows",
        description="Enable/disable Snap windows.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("multitasking.set_snap_windows"),
    ),
    "multitasking.set_title_bar_shake": Tool(
        name="multitasking.set_title_bar_shake",
        description="Enable/disable Title bar window shake (Aero Shake).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("multitasking.set_title_bar_shake"),
    ),
    "multitasking.set_alt_tab_tabs": Tool(
        name="multitasking.set_alt_tab_tabs",
        description="Set Alt+Tab tabs from apps: dont_show | 3 | 5 | 20.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("multitasking.set_alt_tab_tabs"),
    ),
        "advanced.get_state": Tool(
        name="advanced.get_state",
        description="Read System > Advanced toggles (End Task + File Explorer options).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("advanced.get_state"),
    ),
    "advanced.set_end_task_in_taskbar": Tool(
        name="advanced.set_end_task_in_taskbar",
        description="Enable/disable End Task in taskbar right-click menu.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_end_task_in_taskbar"),
    ),
    "advanced.set_show_file_extensions": Tool(
        name="advanced.set_show_file_extensions",
        description="Show/hide file extensions in File Explorer.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_show_file_extensions"),
    ),
    "advanced.set_show_hidden_and_system_files": Tool(
        name="advanced.set_show_hidden_and_system_files",
        description="Show/hide hidden + system files in File Explorer.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_show_hidden_and_system_files"),
    ),
    "advanced.set_show_full_path_in_title_bar": Tool(
        name="advanced.set_show_full_path_in_title_bar",
        description="Show/hide full path in File Explorer title bar.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_show_full_path_in_title_bar"),
    ),
    "advanced.set_show_empty_drives": Tool(
        name="advanced.set_show_empty_drives",
        description="Show/hide empty drives in File Explorer.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_show_empty_drives"),
    ),
    "advanced.set_show_run_as_different_user_in_start": Tool(
        name="advanced.set_show_run_as_different_user_in_start",
        description="Show/hide 'Run as different user' in Start menu.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("advanced.set_show_run_as_different_user_in_start"),
    ),
    "troubleshoot.list": Tool(
        name="troubleshoot.list",
        description="List known troubleshooters and whether msdt.exe is available.",
        func=_runner_tool("troubleshoot.list"),
        risk=RiskLevel.LOW,
    ),
    "troubleshoot.open_settings": Tool(
        name="troubleshoot.open_settings",
        description="Open Windows Settings → System → Troubleshoot.",
        func=_runner_tool("troubleshoot.open_settings"),
        risk=RiskLevel.LOW,
    ),
    "troubleshoot.run": Tool(
        name="troubleshoot.run",
        description="Run a troubleshooter (best-effort). May launch legacy msdt UI or fall back to Settings.",
        func=_runner_tool("troubleshoot.run"),
        risk=RiskLevel.MEDIUM,
    ),
    "about.get_state": Tool(
        name="about.get_state",
        description="Get About info for this PC (CPU/RAM/GPU/Windows version, etc).",
        func=_runner_tool("about.get_state"),
        risk=RiskLevel.LOW,
    ),
    "about.rename_pc": Tool(
        name="about.rename_pc",
        description="Rename this PC (admin + restart required, restart needed to fully apply).",
        func=_runner_tool("about.rename_pc"),
        risk=RiskLevel.HIGH,
    ),
    "network.list_wifi_networks": Tool(
        name="network.list_wifi_networks",
        description="Scan nearby Wi-Fi networks and mark which ones are already saved on this PC.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.list_wifi_networks"),
    ),
    "network.list_wifi_networks": Tool(
        name="network.list_wifi_networks",
        description="Scan nearby Wi-Fi networks and flag which ones are saved profiles on this PC.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.list_wifi_networks"),
    ),
    "network.get_data_usage_total": Tool(
        name="network.get_data_usage_total",
        description="Get total data usage counters (bytes sent/received) for network adapters since boot.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.get_data_usage_total"),
    ),
    "network.get_data_usage_current_wifi": Tool(
        name="network.get_data_usage_current_wifi",
        description="Get data usage for the current Wi-Fi SSID (session tracked) plus adapter totals since boot.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.get_data_usage_current_wifi"),
    ),
    "network.get_connection_properties": Tool(
        name="network.get_connection_properties",
        description="Show network hardware and connection properties (adapters + IP config + Wi-Fi interface).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.get_connection_properties"),
    ),
    "network.hotspot_status": Tool(
        name="network.hotspot_status",
        description="Show Mobile Hotspot status and SSID (does not show password).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("network.hotspot_status"),
    ),
    "network.hotspot_toggle": Tool(
        name="network.hotspot_toggle",
        description="Turn Mobile Hotspot on/off (Windows 10/11).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("network.hotspot_toggle"),
    ),
    "bluetooth.scan_nearby": Tool(
        name="bluetooth.scan_nearby",
        description="Scan for nearby Bluetooth devices (BLE advertising). Flags which are already paired (best-effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("bluetooth.scan_nearby"),
    ),
    # ----- Date & time (Time & language) -----
    "time.get_state": Tool(
        name="time.get_state",
        description="Get Date & Time / Time zone settings status (best-effort).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("time.get_state"),
    ),
    "time.set_auto_time": Tool(
        name="time.set_auto_time",
        description='Turn "Set time automatically" on/off.',
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("time.set_auto_time"),
    ),
    "time.set_auto_timezone": Tool(
        name="time.set_auto_timezone",
        description='Turn "Set time zone automatically" on/off.',
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("time.set_auto_timezone"),
    ),
    "time.set_timezone": Tool(
        name="time.set_timezone",
        description="Set the current time zone (only when auto time zone is off).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("time.set_timezone"),
    ),
    "time.sync_now": Tool(
        name="time.sync_now",
        description="Sync time now (w32tm /resync).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("time.sync_now"),
    ),
    "time.set_show_systray_datetime": Tool(
        name="time.set_show_systray_datetime",
        description="Show/hide time and date in the system tray (taskbar clock).",
        risk=RiskLevel.LOW,
        func=_runner_tool("time.set_show_systray_datetime"),
    ),
    "time.set_show_clock_notification_center": Tool(
        name="time.set_show_clock_notification_center",
        description="Show/hide time in the Notification Center.",
        risk=RiskLevel.LOW,
        func=_runner_tool("time.set_show_clock_notification_center"),
    ),
    # ----- Gaming -----
    "gaming.get_game_mode": Tool(
        name="gaming.get_game_mode",
        description="Get Game Mode status.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("gaming.get_game_mode"),
    ),
    "gaming.set_game_mode": Tool(
        name="gaming.set_game_mode",
        description="Turn Game Mode on/off.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("gaming.set_game_mode"),
    ),
    # ----- Accessibility (Vision: text size + visual effects) -----
    "accessibility.get_vision_state": Tool(
        name="accessibility.get_vision_state",
        description="Get Accessibility (Vision) state: text size + visual effects.",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("accessibility.get_vision_state"),
    ),
    "accessibility.set_text_size": Tool(
        name="accessibility.set_text_size",
        description="Set Accessibility text size (percent).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_text_size"),
    ),
    "accessibility.set_always_show_scrollbars": Tool(
        name="accessibility.set_always_show_scrollbars",
        description="Toggle Always show scrollbars.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_always_show_scrollbars"),
    ),
    "accessibility.set_transparency_effects": Tool(
        name="accessibility.set_transparency_effects",
        description="Toggle Transparency effects.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_transparency_effects"),
    ),
    "accessibility.set_animation_effects": Tool(
        name="accessibility.set_animation_effects",
        description="Toggle Animation effects.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_animation_effects"),
    ),
    "accessibility.set_dismiss_notifications_after": Tool(
        name="accessibility.set_dismiss_notifications_after",
        description="Set dismiss notifications after X seconds.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_dismiss_notifications_after"),
    ),
        # ----- Accessibility: Mouse pointer & touch -----
    "accessibility.get_mouse_touch_state": Tool(
        name="accessibility.get_mouse_touch_state",
        description="Get mouse pointer and touch accessibility settings (style, size, trails, indicator, touch feedback).",
        risk=RiskLevel.LOW,
        func=_runner_tool("accessibility.get_mouse_touch_state"),
    ),

    "accessibility.set_mouse_pointer_style": Tool(
        name="accessibility.set_mouse_pointer_style",
        description="Set mouse pointer style: white/black/inverted/custom (optional custom color).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_style"),
    ),
    "accessibility.set_mouse_pointer_color": Tool(
        name="accessibility.set_mouse_pointer_color",
        description="Set mouse pointer custom color (purple/lime/yellow/gold/pink/turquoise/green).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_color"),
    ),
    "accessibility.set_mouse_pointer_size": Tool(
        name="accessibility.set_mouse_pointer_size",
        description="Set mouse pointer size (best-effort range 1–15).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_size"),
    ),

    "accessibility.set_mouse_indicator": Tool(
        name="accessibility.set_mouse_indicator",
        description="Toggle mouse indicator (Ctrl key circle).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_indicator"),
    ),
    "accessibility.set_mouse_pointer_trails": Tool(
        name="accessibility.set_mouse_pointer_trails",
        description="Toggle mouse pointer trails (optional length).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_trails"),
    ),
    "accessibility.set_mouse_pointer_trails_length": Tool(
        name="accessibility.set_mouse_pointer_trails_length",
        description="Set mouse pointer trails length (1–20).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_trails_length"),
    ),
    "accessibility.set_mouse_pointer_shadow": Tool(
        name="accessibility.set_mouse_pointer_shadow",
        description="Toggle mouse pointer shadow.",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_mouse_pointer_shadow"),
    ),

    "accessibility.set_touch_indicator": Tool(
        name="accessibility.set_touch_indicator",
        description="Toggle touch indicator (touch circle).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_touch_indicator"),
    ),
    "accessibility.set_touch_indicator_enhanced": Tool(
        name="accessibility.set_touch_indicator_enhanced",
        description="Toggle 'make the circle darker and larger' (requires touch indicator ON).",
        risk=RiskLevel.MEDIUM,
        func=_runner_tool("accessibility.set_touch_indicator_enhanced"),
    ),

    # ----- MK3.3 Filesystem (workspace sandbox) -----
    "fs.list_dir": Tool(
        name="fs.list_dir",
        description="List files/folders inside workspace/ (safe sandbox).",
        risk=RiskLevel.READ_ONLY,
        func=fs_list_dir,
    ),
    "fs.stat": Tool(
        name="fs.stat",
        description="Show file/folder info inside workspace/.",
        risk=RiskLevel.READ_ONLY,
        func=fs_stat,
    ),
    "fs.mkdir": Tool(
        name="fs.mkdir",
        description="Create a folder inside workspace/.",
        risk=RiskLevel.MEDIUM,
        func=fs_mkdir,
    ),
    "fs.copy": Tool(
        name="fs.copy",
        description="Copy a file/folder inside workspace/.",
        risk=RiskLevel.MEDIUM,
        func=fs_copy,
    ),
    "fs.move": Tool(
        name="fs.move",
        description="Move/rename a file/folder inside workspace/.",
        risk=RiskLevel.HIGH,
        func=fs_move,
    ),

    # ----- MK3.4 Dev Mode sandbox patch pipeline -----
    "dev.status": Tool(
        name="dev.status",
        description="Show Dev Mode sandbox state (pending patch, last test, sandbox path).",
        risk=RiskLevel.READ_ONLY,
        func=dev_status,
    ),
    "dev.sandbox_reset": Tool(
        name="dev.sandbox_reset",
        description="Rebuild sandbox by copying repo into workspace/repo_sandbox and run checks.",
        risk=RiskLevel.MEDIUM,
        func=dev_sandbox_reset,
    ),
    "dev.propose_patch": Tool(
        name="dev.propose_patch",
        description="Apply a unified diff to the sandbox only, run checks, and store as pending.",
        risk=RiskLevel.HIGH,
        func=dev_propose_patch,
    ),
    "dev.discard_patch": Tool(
        name="dev.discard_patch",
        description="Discard pending patch and reset sandbox.",
        risk=RiskLevel.MEDIUM,
        func=dev_discard_patch,
    ),
    "dev.apply_patch": Tool(
        name="dev.apply_patch",
        description="Apply the pending patch to the real repo (backs up files, runs checks).",
        risk=RiskLevel.CRITICAL,
        func=dev_apply_patch,
    ),












        "uia.get_status": Tool(
        name="uia.get_status",
        description="UI Automation status (scaffolded; disabled by default).",
        risk=RiskLevel.READ_ONLY,
        func=_runner_tool("uia.get_status"),
    ),

})



