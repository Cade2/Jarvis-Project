from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, Any, Dict
from datetime import datetime


class RiskLevel(Enum):
    LOW = auto()
    MEDIUM = auto()
    HIGH = auto()


@dataclass
class Tool:
    """Represents an action the agent is allowed to perform."""
    name: str
    description: str
    risk: RiskLevel
    func: Callable[[Dict[str, Any]], Any]


def should_confirm(tool: Tool, params: Dict[str, Any]) -> bool:
    """
    Decide if this tool call should require user confirmation.
    v0: purely rule-based using the tool's risk level.
    """
    if tool.risk == RiskLevel.HIGH:
        return True
    if tool.risk == RiskLevel.MEDIUM:
        return True
    # LOW risk → no confirmation needed
    return False


def log_action(tool: Tool, params: Dict[str, Any], outcome: str) -> None:
    """
    Append a line to a local audit log file.
    outcome examples: 'success', 'cancelled', 'error: ...'
    """
    line = (
        f"{datetime.now().isoformat()} | "
        f"{tool.name} | "
        f"{params} | "
        f"{outcome}\n"
    )
    with open("audit.log", "a", encoding="utf-8") as f:
        f.write(line)
