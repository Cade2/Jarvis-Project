from typing import Dict, Any
from datetime import datetime, timedelta
import re
from .policy import Policy


from .tools import TOOLS
from .safety import should_confirm, log_action, Tool
from .models import ChatModel

# Single shared chat model instance (local LLM)
_policy = Policy.load()
_chat_model = ChatModel()   # <-- this is our "brain"


def _run_tool(tool_name: str, params: Dict[str, Any]) -> None:
    tool: Tool = TOOLS[tool_name]

    # Safety: confirmation if required
    if should_confirm(tool, params):
        print(f"Jarvis: I plan to use '{tool.name}' with parameters: {params}")

        cfg = _policy.confirm_config()
        type_risks = set(cfg.get("type_to_confirm_for_risks", []) or [])
        phrase_high = cfg.get("type_phrase_high", "CONFIRM")
        phrase_critical = cfg.get("type_phrase_critical", "CONFIRM-CRITICAL")

        if tool.risk.name in type_risks:
            phrase = phrase_critical if tool.risk.name == "CRITICAL" else phrase_high
            typed = input(f"Type '{phrase}' to proceed: ").strip()
            if typed != phrase:
                print("Jarvis: Okay, I cancelled that action.")
                log_action(tool, params, "cancelled")
                return
        else:
            choice = input("Proceed? (y/n): ").strip().lower()
            if choice not in ("y", "yes"):
                print("Jarvis: Okay, I cancelled that action.")
                log_action(tool, params, "cancelled")
                return

    # Execute the tool and log
    try:
        result = tool.func(params)
        log_action(tool, params, "success")
        if result is not None:
            print(f"Jarvis: Tool returned: {result}")
    except Exception as exc:
        print(f"Jarvis: Something went wrong while executing the tool: {exc}")
        log_action(tool, params, f"error: {exc}")



def _extract_when_from_text(text: str) -> str:
    """
    Very small, safe 'natural language' time parser for reminders.

    It understands things like:
      - "at 9pm" / "at 21:00" / "at 9:30 am"
      - "tomorrow"
      - "on Monday", "on tuesday", etc.

    It returns a simple string like:
      - "2025-12-15 21:00"
      - "2025-12-16"
    or "unspecified time" if we cannot figure it out.
    """
    lower = text.lower()
    now = datetime.now()

    # 1) Look for "at <time>" patterns
    m = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)

        if ampm:
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0

        # If "tomorrow" appears, schedule for tomorrow; otherwise today
        day = now
        if "tomorrow" in lower:
            day = day + timedelta(days=1)

        dt = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return dt.strftime("%Y-%m-%d %H:%M")

    # 2) Plain "tomorrow" without a time
    if "tomorrow" in lower:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    # 3) Day-of-week like "on monday"
    weekdays = ["monday", "tuesday", "wednesday",
                "thursday", "friday", "saturday", "sunday"]

    for idx, name in enumerate(weekdays):
        if f"on {name}" in lower or lower.strip().startswith(name):
            days_ahead = (idx - now.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7  # next occurrence of that weekday
            target = now + timedelta(days=days_ahead)
            return target.strftime("%Y-%m-%d")

    # 4) Fallback
    return "unspecified time"


def handle_user_message(user_message: str) -> None:
    """
    Main entrypoint for a single user message.

    v0 routing (MK1.6):

    - If the message starts with:
        * "summarise:" / "summarize:" -> summarise the following text
        * "remind me"                 -> create_reminder tool
        * "open "                     -> open_application tool
        * "close "                    -> close_application tool
      or (after normalising spaces) exactly matches:
        * "list reminders" / "show reminders" / "show my reminders"
        * "show activity" / "show activity last N"
      then we run the corresponding tool with safety + logging.
    - Otherwise, we fall back to the local chat model.
    """

    raw = user_message

    if not raw.strip():
        print("Jarvis: I didn't receive any input.")
        return

    text_lower = raw.strip().lower()
    normalized = " ".join(raw.split()).lower()

    if normalized in ("system info", "my system", "pc info"):
        _run_tool("system.get_info", {})
        return

    if normalized in ("storage", "disk space", "drive space"):
        _run_tool("system.get_storage", {})
        return

    if normalized in ("list installed apps", "installed apps", "apps list"):
        _run_tool("apps.list_installed", {})
        return


    # 🔹 NEW: help / commands
    if normalized in ("help", "commands", "what can you do", "what can you do?"):
        print("Jarvis: Here’s what I can do right now:")
        print("  • General chat  → just type anything")
        print("  • Summaries     → summarise: <text>")
        print("  • Reminders     → remind me to <do X> at <time>")
        print("                    list reminders")
        print("                    delete reminder <number>")
        print("                    clear reminders   (with confirmation)")
        print("  • Apps          → open <app name>")
        print("                    close <app name>")
        print("  • Activity log  → show activity")
        print("                    show activity last <N>")
        return
    
        # 🔹 MK2 quick commands (runner-backed tools)
    if normalized in ("system info", "my system", "pc info"):
        _run_tool("system.get_info", {})
        return

    if normalized in ("storage", "disk space", "drive space"):
        _run_tool("system.get_storage", {})
        return

    if normalized in ("list installed apps", "installed apps", "apps list"):
        _run_tool("apps.list_installed", {})
        return

        # Open Windows Settings deep links
    if text_lower.startswith("open settings "):
        target = raw.strip()[len("open settings "):].strip()
        if not target:
            target = "system"
        _run_tool("settings.open", {"target": target})
        return

    if normalized.startswith("settings "):
        target = raw.strip()[len("settings "):].strip()
        if not target:
            target = "system"
        _run_tool("settings.open", {"target": target})
        return


    # 0) Summarise text
    if text_lower.startswith("summarise:") or text_lower.startswith("summarize:"):
        parts = raw.split(":", 1)
        if len(parts) < 2 or not parts[1].strip():
            print("Jarvis: You asked me to summarise, but didn't give any text.")
            return

        content = parts[1].strip()
        print("Jarvis: (summarising)...")

        reply = _chat_model.chat([
            "You are a helpful, concise assistant.",
            "Summarise this text clearly and briefly:",
            content,
            "Summary:",
        ])
        print(f"Jarvis: {reply}")
        return

    # 1) Reminders
    if text_lower.startswith("remind me"):
        when_str = _extract_when_from_text(raw)
        params = {
            "text": raw,
            "when": when_str,
        }
        _run_tool("create_reminder", params)
        return

    # 2) Open application
    if text_lower.startswith("open "):
        app_name = raw.strip()[len("open "):].strip()
        if not app_name:
            print("Jarvis: You asked me to open something, but I don't know which app.")
            return

        params = {"app_name": app_name}
        _run_tool("open_application", params)
        return

    # 3) Close application (HIGH risk)
    if text_lower.startswith("close "):
        app_name = raw.strip()[len("close "):].strip()
        if not app_name:
            print("Jarvis: You asked me to close something, but I don't know which app.")
            return

        params = {"app_name": app_name}
        _run_tool("close_application", params)
        return

    # 4) List reminders (space-insensitive)
    if normalized in ("list reminders", "show reminders", "show my reminders"):
        _run_tool("list_reminders", {})
        return

    

    # 5) Show recent activity from audit.log
    if (
        normalized in ("show activity", "show audit log", "show log")
        or "audit.log" in text_lower
        or "activity log" in text_lower
    ):
        
      
        
        # Allow optional "last N" style, e.g. "show activity last 5"
        limit = 10
        match = re.search(r"last\s+(\d+)", text_lower)
        if match:
            try:
                limit = int(match.group(1))
            except ValueError:
                limit = 10

        _run_tool("show_activity", {"limit": limit})
        return

    # 6) Delete a single reminder: "delete reminder 2", "remove reminder 3", etc.
    if text_lower.startswith("delete reminder") or text_lower.startswith("remove reminder"):
        # Find the first integer in the message
        match = re.search(r"(\d+)", text_lower)
        if not match:
            print("Jarvis: Please tell me which reminder number to delete (e.g. 'delete reminder 2').")
            return

        index = int(match.group(1))
        _run_tool("delete_reminder", {"index": index})
        return

    # 7) Clear all reminders
    if normalized in ("clear reminders", "delete all reminders", "remove all reminders"):
        _run_tool("clear_reminders", {})
        return


    # 8) No matching command → general chat
    print("Jarvis: (thinking)...")
    reply = _chat_model.chat([
        "You are a helpful, concise assistant named Jarvis.",
        f"User: {user_message}",
        "Assistant:",
    ])
    print(f"Jarvis: {reply}")
