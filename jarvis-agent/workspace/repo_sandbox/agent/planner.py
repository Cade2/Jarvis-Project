import json
from typing import Dict, Any
from .safety import Tool


def build_planner_prompt(user_message: str, tools: Dict[str, Tool]) -> str:
    """
    Build the text prompt that will be sent to the local LLM planner.

    The LLM's job:
    - Read the available tools and their descriptions.
    - Read the user message.
    - Decide which single tool to use (or 'none').
    - Return ONLY a JSON object with 'tool_name' and 'params'.
    """
    tool_descriptions = []
    for name, tool in tools.items():
        tool_descriptions.append(f"- {name}: {tool.description}")
    tools_text = "\n".join(tool_descriptions)

    allowed_tool_names = ", ".join(list(tools.keys()) + ["none"])

    prompt = f"""
You are the planning module for a local, privacy-first desktop assistant.

You are given:
1) A list of tools you are allowed to use.
2) A user message describing what they want.

Your task:
- Choose exactly ONE tool from the list (or 'none' if no tool applies).
- Decide what parameters that tool should receive.
- Think safely: prefer non-destructive actions when the intent is unclear.

Available tools:
{tools_text}

User message:
\"\"\"{user_message}\"\"\"

Respond with a single valid JSON object only, using this format:

{{
  "tool_name": "<one of: {allowed_tool_names}>",
  "params": {{
    // key-value pairs needed for that tool
  }}
}}

Notes:
- If no tool is appropriate, use "tool_name": "none" and an empty params object.
- Do NOT include any explanation outside the JSON.
"""
    return prompt.strip()


def parse_planner_output(output: str) -> Dict[str, Any]:
    """
    Take the raw string returned by the LLM and try to extract:
    - tool_name
    - params (dict)

    If parsing fails or the model output is invalid, fall back to 'none'.
    """
    try:
        data = json.loads(output)
        tool_name = data.get("tool_name")
        params = data.get("params", {}) or {}

        if tool_name == "none":
            tool_name = None

        if not isinstance(params, dict):
            params = {}

        return {"tool_name": tool_name, "params": params}
    except json.JSONDecodeError:
        # If the model returns messy output, we don't execute any tool
        return {"tool_name": None, "params": {}}
