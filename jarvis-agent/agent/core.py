from typing import Dict, Any

from .tools import TOOLS
from .safety import should_confirm, log_action, Tool
from .planner import build_planner_prompt, parse_planner_output
from .models import DummyPlannerModel


# Toggle this to True later when we have a real model
USE_MODEL_PLANNER = False

# Single instance of the planner model (will be replaced with real LLM later)
_planner_model = DummyPlannerModel()


def plan_action_rule_based(user_message: str) -> Dict[str, Any]:
    """
    v0 fallback planner using simple rules.

    This is what we already had, just renamed.
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
    if "tool_name" not in plan:
        plan["tool_name"] = None
    if "params" not in plan:
        plan["params"] = {}

    return plan


def plan_action(user_message: str) -> Dict[str, Any]:
    """
    Wrapper that decides whether to use the model-based planner
    or the simple rule-based planner.

    For now, we keep USE_MODEL_PLANNER = False so behavior is exactly
    the same as before. Later, when we plug in a real local LLM,
    we can switch this flag.
    """
    if USE_MODEL_PLANNER:
        plan = plan_action_with_model(user_message)
        # If the model couldn't decide, fall back to rules
        if plan.get("tool_name"):
            return plan

    # Default: rule-based
    return plan_action_rule_based(user_message)


def handle_user_message(user_message: str) -> None:
    """
    Main entrypoint for a single user message.

    - Plans an action (tool + params)
    - Applies safety checks (confirmation if needed)
    - Executes the tool
    - Logs the result
    """
    plan = plan_action(user_message)
    tool_name = plan["tool_name"]
    params = plan["params"]

    if not tool_name:
        print("Jarvis: I’m not sure what action to take yet, but I noted your message.")
        return

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
