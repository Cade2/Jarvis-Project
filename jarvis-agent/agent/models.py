import json
from typing import Dict, Any


class DummyPlannerModel:
    """
    Placeholder for a future local LLM-based planner.

    For now, this class pretends to be an LLM by returning
    hard-coded JSON based on very simple keyword checks.

    Later, we'll replace the body of `generate` with a real call to
    a local model (e.g. via llama.cpp or another runtime).
    """

    def generate(self, prompt: str) -> str:
        # Naive keyword-based stub, just for structure/testing.
        lower = prompt.lower()

        # Very rough detection. In a real model, we'd parse the user message
        # inside the prompt instead of looking at the prompt text itself.
        if "remind me" in lower:
            return json.dumps({
                "tool_name": "create_reminder",
                "params": {
                    "text": "Reminder created by DummyPlannerModel",
                    "when": "tomorrow 18:00"
                }
            })

        if "open " in lower:
            return json.dumps({
                "tool_name": "open_application",
                "params": {
                    "app_name": "notepad"
                }
            })

        # Default: no tool
        return json.dumps({
            "tool_name": "none",
            "params": {}
        })
