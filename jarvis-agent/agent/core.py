from typing import Dict, Any
from datetime import datetime, timedelta
import re

from .tools import TOOLS
from .safety import should_confirm, log_action, Tool
from .models import ChatModel

# Single shared chat model instance (local LLM)
_chat_model = ChatModel()   # <-- this is our "brain"


def _run_tool(tool_name: str, params: Dict[str, Any]) -> None:
    """
    Helper to run a tool with safety + logging.
    """
    tool: Tool = TOOLS[tool_name]

    # Safety: confirmation if required
    if should_confirm(tool, params):
        print(f"Jarvis: I plan to use '{tool.name}' with parameters: {params}")
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

    v0 routing (MK1.7):

    - If the message starts with:
        * "summarise:" / "summarize:" -> summarise the following text
        * "remind me"                 -> create_reminder tool
        * "open "                     -> open_application tool
        * "close "                    -> close_application tool
      or (after normalising spaces) exactly matches:
        * "list reminders" / "show reminders" / "show my reminders"
      then we run the corresponding tool with safety + logging.
    - Otherwise, we fall back to the local chat model.
    """

    raw = user_message

    # Ignore empty / whitespace-only input
    if not raw.strip():
        print("Jarvis: I didn't receive any input.")
        return

    text_lower = raw.strip().lower()
    normalized = " ".join(raw.split()).lower()  # collapse multiple spaces

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

    # 5) No matching command → general chat
    print("Jarvis: (thinking)...")
    reply = _chat_model.chat([
        "You are a helpful, concise assistant named Jarvis.",
        f"User: {user_message}",
        "Assistant:",
    ])
    print(f"Jarvis: {reply}")
