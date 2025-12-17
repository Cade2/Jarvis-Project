from __future__ import annotations

import os
import sys
import time
import signal
import subprocess
from pathlib import Path

from .runner_client import RunnerClient

RUNNER_PID_FILE = Path("logs/runner.pid")


def ensure_runner_started() -> None:
    client = RunnerClient()
    if client.health():
        return

    project_root = Path(__file__).resolve().parents[1]
    env = os.environ.copy()
    env["PYTHONPATH"] = str(project_root)

    cmd = [sys.executable, "-m", "runner.server"]

    # Start runner
    proc = subprocess.Popen(
        cmd,
        cwd=str(project_root),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0),
    )

    # Save PID so we can stop it later if needed
    RUNNER_PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    try:
        RUNNER_PID_FILE.write_text(str(proc.pid), encoding="utf-8")
    except Exception:
        pass

    # Wait until healthy
    for _ in range(20):
        if client.health():
            return
        time.sleep(0.15)

    raise RuntimeError("Runner failed to start. Try: python -m runner.server")


def stop_runner() -> None:
    """
    Best-effort stop for a runner that we started (tracked via PID file).
    """
    if not RUNNER_PID_FILE.exists():
        return

    try:
        pid = int(RUNNER_PID_FILE.read_text(encoding="utf-8").strip())
    except Exception:
        return

    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
        else:
            os.kill(pid, signal.SIGTERM)
    finally:
        try:
            RUNNER_PID_FILE.unlink()
        except Exception:
            pass
