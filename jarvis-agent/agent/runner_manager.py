from __future__ import annotations
import os
import sys
import time
import subprocess
from pathlib import Path

from .runner_client import RunnerClient

def ensure_runner_started() -> None:
    client = RunnerClient()
    if client.health():
        return

    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    cmd = [sys.executable, "-m", "runner.server"]
    subprocess.Popen(
        cmd,
        cwd=str(project_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    for _ in range(20):
        if client.health():
            return
        time.sleep(0.15)

    raise RuntimeError("Runner failed to start. Try: python -m runner.server")
