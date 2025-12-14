from typing import Dict, Any
import subprocess
import sys
import json
from pathlib import Path

from .safety import Tool, RiskLevel

# ---- Reminder storage helpers ----

REMINDERS_FILE = Path("reminders.json")

AUDIT_FILE = Path("audit.log")  # same file safety.log_action writes to

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
    Show the most recent actions from the local audit.log file.
    """
    limit = int(params.get("limit", 10))

    if not AUDIT_FILE.exists():
        print("[ACTIVITY] No audit.log file found yet.")
        return

    try:
        with open(AUDIT_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except Exception as e:
        print(f"[ACTIVITY] Failed to read audit.log: {e}")
        return

    if not lines:
        print("[ACTIVITY] audit.log is empty.")
        return

    print("[ACTIVITY] Recent actions:")
    for idx, line in enumerate(lines[-limit:], start=1):
        print(f"{idx}. {line.strip()}")



def open_application(params: Dict[str, Any]):
    """
    Try to open a desktop application by name.

    On Windows, this uses the 'start' command with a small alias map
    for common apps (Notepad, Chrome, Edge, etc.).
    On macOS, it uses 'open -a'.
    On Linux, it tries to run the app directly.
    """
    app_name = params.get("app_name")
    if not app_name:
        print("No app_name provided.")
        return

    print(f"[OPEN APPLICATION] {app_name}")

    try:
        if sys.platform.startswith("win"):
            app_name_lower = app_name.lower().strip()

            # Common friendly-name -> command aliases
            aliases = {
                "notepad": "notepad",
                "microsoft teams": "Teams",      # may work if Teams is on PATH
                "teams": "Teams",
                "google chrome": "chrome",
                "chrome": "chrome",
                "edge": "msedge",
                "microsoft edge": "msedge",
            }

            target = aliases.get(app_name_lower, app_name)

            # Use start "" "<target>" so Windows tries to resolve it
            cmd = f'start "" "{target}"'
            subprocess.Popen(cmd, shell=True)

        elif sys.platform == "darwin":
            # macOS: open by app name
            subprocess.Popen(["open", "-a", app_name])

        else:
            # Linux / other: try to run directly
            subprocess.Popen([app_name])

    except Exception as e:
        print(f"Failed to open application: {e}")


def close_application(params: Dict[str, Any]):
    """
    Try to close a desktop application by name.

    v0: very conservative and simple.
    - On Windows, we map common app names to process names and call taskkill.
    - For other platforms, this is currently a no-op.
    """
    app_name = params.get("app_name")
    if not app_name:
        print("No app_name provided.")
        return

    print(f"[CLOSE APPLICATION] {app_name}")

    # Very simple mapping for now. We can extend this later.
    app_name_lower = app_name.lower()

    if sys.platform.startswith("win"):
        # Map friendly names to process names
        process_map = {
            "notepad": "notepad.exe",
        }
        process_name = process_map.get(app_name_lower)

        if not process_name:
            print("Sorry, I don't know how to safely close that app yet.")
            return

        try:
            # /F = force; /IM = by image name
            subprocess.run(
                ["taskkill", "/IM", process_name, "/F"],
                check=False,
                shell=True,
            )
        except Exception as e:
            print(f"Failed to close application: {e}")
    else:
        print("Close application is not implemented for this OS yet.")


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


