from typing import Dict, Any
import subprocess
import sys
import json
from pathlib import Path

from .safety import Tool, RiskLevel

# ---- Reminder storage helpers ----

REMINDERS_FILE = Path("reminders.json")


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


def list_reminders(params: Dict[str, Any]):
    """
    List all saved reminders from reminders.json.
    """
    reminders = _load_reminders()
    if not reminders:
        print("[REMINDERS] No saved reminders.")
        return

    print("[REMINDERS]")
    for idx, r in enumerate(reminders, start=1):
        text = r.get("text", "")
        when = r.get("when", "")
        print(f"{idx}. {text} @ {when}")


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
    "list_reminders": Tool(
        name="list_reminders",
        description="Show all reminders saved locally on this device.",
        risk=RiskLevel.LOW,
        func=list_reminders,
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
}
