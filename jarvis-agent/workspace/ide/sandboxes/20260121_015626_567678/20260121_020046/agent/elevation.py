from __future__ import annotations
from typing import Any, Dict
import os
import sys
import subprocess
from pathlib import Path

from .runner_manager import stop_runner  # we'll add this if you don't have it yet


def relaunch_runner_elevated(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Relaunch the runner as Administrator (UAC prompt).
    Safely stops any runner we started (if tracked) before relaunching.
    """
    if os.name != "nt":
        return {"error": "runner.relaunch_elevated is only supported on Windows."}

    # Best effort: stop any existing runner we started
    try:
        stop_runner()
    except Exception:
        pass

    project_root = Path(__file__).resolve().parents[1]  # .../jarvis-agent
    py = sys.executable  # points to conda env python.exe
    args = "-m runner.server"

    # Start elevated runner (UAC prompt)
    ps = (
        f'$argsList = @("-m","runner.server"); '
        f'Start-Process -Verb RunAs -WindowStyle Hidden '
        f'-FilePath "{py}" -ArgumentList $argsList '
        f'-WorkingDirectory "{project_root}" | Out-Null'
    )


    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps],
        capture_output=True,
        text=True,
    )

    if p.returncode != 0:
        return {"result": {"started": False, "exit_code": p.returncode, "stderr": (p.stderr or "").strip()}}

    return {"result": {"started": True, "note": "Runner relaunch requested (UAC prompt)."}}
