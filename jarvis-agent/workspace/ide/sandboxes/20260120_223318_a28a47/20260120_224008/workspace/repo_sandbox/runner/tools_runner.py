from __future__ import annotations
from typing import Any, Dict
import os

def runner_is_elevated(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns whether the RUNNER process is elevated (admin) on Windows.
    """
    if os.name != "nt":
        return {"result": {"elevated": False, "supported": False}}

    try:
        import ctypes
        elevated = bool(ctypes.windll.shell32.IsUserAnAdmin())
        return {"result": {"elevated": elevated, "supported": True}}
    except Exception as e:
        return {"result": {"elevated": False, "supported": True, "error": str(e)}}
