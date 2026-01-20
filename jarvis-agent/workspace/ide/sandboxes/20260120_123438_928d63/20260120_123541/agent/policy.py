from __future__ import annotations
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict

try:
    import yaml  # PyYAML
except Exception:
    yaml = None


@dataclass
class Policy:
    data: Dict[str, Any]

    @classmethod
    def load(cls, path: str | Path = "config/policy.yaml") -> "Policy":
        path = Path(path)
        if not path.exists() or yaml is None:
            return cls({
                "allow_domains": ["system", "storage", "apps", "runner"],
                "ui_automation": {"enabled": False, "allowlisted_apps": []},
                "confirm": {
                    "y_n_for_risks": ["MEDIUM"],
                    "type_to_confirm_for_risks": ["HIGH", "CRITICAL"],
                    "type_phrase_high": "CONFIRM",
                    "type_phrase_critical": "CONFIRM-CRITICAL",
                },
                "elevation_required_tools": [],
            })
        return cls(yaml.safe_load(path.read_text(encoding="utf-8")))

    def allow_domains(self) -> set[str]:
        return set(self.data.get("allow_domains", []) or [])

    def confirm_config(self) -> Dict[str, Any]:
        return dict(self.data.get("confirm", {}) or {})

    def domain_from_tool(self, tool_name: str) -> str:
        return tool_name.split(".", 1)[0].strip().lower() if "." in tool_name else "unknown"

    def is_domain_allowed(self, tool_name: str) -> bool:
        return self.domain_from_tool(tool_name) in self.allow_domains()

    def ui_automation_enabled(self) -> bool:
        return bool(self.data.get("ui_automation", {}).get("enabled", False))

    def ui_automation_allowlist(self) -> set[str]:
        return set(self.data.get("ui_automation", {}).get("allowlisted_apps", []) or [])
