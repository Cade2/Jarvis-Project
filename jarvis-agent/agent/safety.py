from __future__ import annotations
from enum import Enum, auto
from dataclasses import dataclass
from typing import Callable, Any, Dict, Optional
from datetime import datetime
from pathlib import Path

class RiskLevel(Enum):
    READ_ONLY = auto()   # R0
    LOW = auto()         # R1
    MEDIUM = auto()      # R2
    HIGH = auto()        # R3
    CRITICAL = auto()    # R4

@dataclass
class Tool:
    name: str
    description: str
    risk: RiskLevel
    func: Callable[[Dict[str, Any]], Any]

_SESSION_LOG: Optional[Path] = None

def init_audit_session(logs_dir: str = "logs") -> Path:
    """
    Create a brand-new audit file for this Jarvis run.
    """
    global _SESSION_LOG
    Path(logs_dir).mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    _SESSION_LOG = Path(logs_dir) / f"audit_{ts}.log"
    _SESSION_LOG.write_text(f"# Jarvis audit session started {datetime.now().isoformat()}\n", encoding="utf-8")
    return _SESSION_LOG

def get_audit_path() -> Path:
    if _SESSION_LOG is None:
        return init_audit_session()
    return _SESSION_LOG

def should_confirm(tool: Tool, params: Dict[str, Any]) -> bool:
    return tool.risk in (RiskLevel.MEDIUM, RiskLevel.HIGH, RiskLevel.CRITICAL)

def log_action(tool: Tool, params: Dict[str, Any], outcome: str) -> None:
    line = f"{datetime.now().isoformat()} | {tool.name} | {params} | {outcome}\n"
    p = get_audit_path()
    with p.open("a", encoding="utf-8") as f:
        f.write(line)
