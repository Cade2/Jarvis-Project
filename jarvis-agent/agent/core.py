from typing import Dict, Any

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


def handle_user_message(user_message: str) -> None:
    """
    Main entrypoint for a single user message.

    v0 routing (MK1.5):
    - If the message starts with:
        * "remind me"   -> create_reminder tool
        * "open "       -> open_application tool
        * "close "      -> close_application tool
      or exactly matches:
        * "list reminders" / "show reminders" / "show my reminders"
      then we run the corresponding tool with safety + logging.
    - Otherwise, we fall back to the local chat model.
    """

    raw = user_message
    text_lower = raw.strip().lower()

    # 1) Reminders
    if text_lower.startswith("remind me"):
        params = {
            "text": raw,
            # TODO: real date/time parsing in a future version
            "when": "tomorrow 18:00",
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

    # 4) List reminders
    if text_lower in ("list reminders", "show reminders", "show my reminders"):
        _run_tool("list_reminders", {})
        return

    # 5) No matching tool → general chat
    print("Jarvis: (thinking)...")
    reply = _chat_model.chat([
        f"User: {user_message}",
        "Assistant:",
    ])
    print(f"Jarvis: {reply}")
