from typing import Dict, Any

from .tools import TOOLS
from .safety import should_confirm, log_action, Tool
from .planner import build_planner_prompt, parse_planner_output
from .models import DummyPlannerModel, ChatModel


# Toggle this later when we have a real LLM planner wired up
USE_MODEL_PLANNER = False

# Single shared instances
_planner_model = DummyPlannerModel()
_chat_model = ChatModel()   # <-- this is our chat brain


def plan_action_rule_based(user_message: str) -> Dict[str, Any]:
    """
    v0 fallback planner using simple rules.

    This is the original logic we had before introducing the planner.
    """
    text = user_message.lower().strip()

    # Example 1: "remind me to call mom tomorrow at 6pm"
    if text.startswith("remind me"):
        return {
            "tool_name": "create_reminder",
            "params": {
                "text": user_message,
                # Later: parse real date/time. For now, hard-code.
                "when": "tomorrow 18:00",
            },
        }

    # Example 2: "open notepad"
    if text.startswith("open "):
        app_name = user_message[5:].strip()
        return {
            "tool_name": "open_application",
            "params": {"app_name": app_name},
        }

    # No matching tool
    return {"tool_name": None, "params": {}}


def plan_action_with_model(user_message: str) -> Dict[str, Any]:
    """
    Future LLM-based planner.

    - Build a planner prompt listing the tools.
    - Ask the model to return JSON.
    - Parse that JSON safely.
    """
    prompt = build_planner_prompt(user_message, TOOLS)
    raw_output = _planner_model.generate(prompt)
    plan = parse_planner_output(raw_output)

    # Ensure keys exist
    tool_name = plan.get("tool_name")
    params = plan.get("params", {})

    if tool_name == "none":
        tool_name = None

    if not isinstance(params, dict):
        params = {}

    return {"tool_name": tool_name, "params": params}


def plan_action(user_message: str) -> Dict[str, Any]:
    """
    Wrapper that decides whether to use the model-based planner
    or the simple rule-based planner.

    For now, USE_MODEL_PLANNER = False so behavior stays the same
    as the original rule-based logic until we switch it on.
    """
    if USE_MODEL_PLANNER:
        plan = plan_action_with_model(user_message)
        if plan.get("tool_name"):
            return plan

    return plan_action_rule_based(user_message)


def handle_user_message(user_message: str) -> None:
    """
    Main entrypoint for a single user message.

    - Plans an action (tool + params)
    - If a tool is chosen: apply safety checks, execute, and log.
    - If no tool is chosen: fall back to the chat model.
    """
    plan = plan_action(user_message)
    tool_name = plan["tool_name"]
    params = plan["params"]

    # 🔹 NO TOOL → use chat model instead of just "noted your message"
    if not tool_name:
        print("Jarvis: (thinking)...")
        reply = _chat_model.chat([
            f"User: {user_message}",
            "Assistant:"
        ])
        print(f"Jarvis: {reply}")
        return

    # 🔹 TOOL PATH (same as before)
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
