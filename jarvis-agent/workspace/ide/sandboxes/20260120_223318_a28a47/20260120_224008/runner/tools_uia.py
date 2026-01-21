# runner/tools_uia.py
from __future__ import annotations
from typing import Any, Dict
import os

def uia_get_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    UI Automation scaffolding (read-only).
    We keep UIA disabled by default. This tool just reports readiness.
    """
    return {
        "result": {
            "supported": (os.name == "nt"),
            "enabled": False,
            "note": "UI Automation is scaffolded but disabled. Enable it later in policy.yaml: ui_automation.enabled: true",
        }
    }
