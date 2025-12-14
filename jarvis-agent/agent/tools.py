from typing import Dict, Any
import subprocess
import sys
import json
from pathlib import Path
from .policy import Policy
from .runner_manager import ensure_runner_started
from .runner_client import RunnerClient


from .safety import Tool, RiskLevel

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
}

# ---- Runner-backed tools (MK2) ----
from .runner_client import RunnerClient
from .runner_manager import ensure_runner_started
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


})



