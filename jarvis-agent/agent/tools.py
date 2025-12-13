from typing import Dict, Any
import subprocess
import sys

from .safety import Tool, RiskLevel


# ---- Tool implementations ----

def create_reminder(params: Dict[str, Any]):
    """
    v0: just print the reminder. Later: hook into real calendar/reminder APIs.
    """
    text = params.get("text", "No text provided")
    when = params.get("when", "unspecified time")
    print(f"[REMINDER] {text} @ {when}")


def open_application(params: Dict[str, Any]):
    """
    Try to open a desktop application by name.

    On Windows, this uses the 'start' command.
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
            # 'start "" "notepad"' pattern
            subprocess.Popen(["start", "", app_name], shell=True)
        elif sys.platform == "darwin":
            subprocess.Popen(["open", "-a", app_name])
        else:
            subprocess.Popen([app_name])
    except Exception as e:
        print(f"Failed to open application: {e}")


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
        risk=RiskLevel.LOW,
        func=open_application,
    ),
}
