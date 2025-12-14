from __future__ import annotations
from typing import Any, Dict, Optional
import requests

DEFAULT_BASE_URL = "http://127.0.0.1:8765"

class RunnerClient:
    def __init__(self, base_url: str = DEFAULT_BASE_URL) -> None:
        self.base_url = base_url.rstrip("/")

    def health(self) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=0.8)
            return r.status_code == 200
        except Exception:
            return False

    def run_tool(self, tool_name: str, params: Dict[str, Any], approval_token: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"params": params or {}}
        if approval_token:
            payload["approval_token"] = approval_token
        r = requests.post(f"{self.base_url}/tool/{tool_name}", json=payload, timeout=15)
        r.raise_for_status()
        return r.json()
