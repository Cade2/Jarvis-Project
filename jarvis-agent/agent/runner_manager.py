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
    Best-effort stop for a runner that we started.
    Falls back to killing any process listening on port 8765 (Windows).
    """
    killed_any = False

    # 1) Try PID file
    if RUNNER_PID_FILE.exists():
        try:
            pid = int(RUNNER_PID_FILE.read_text(encoding="utf-8").strip())
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], check=False)
            else:
                os.kill(pid, signal.SIGTERM)
            killed_any = True
        except Exception:
            pass
        finally:
            try:
                RUNNER_PID_FILE.unlink()
            except Exception:
                pass

    # 2) Fallback: kill by port (Windows)
    if os.name == "nt":
        try:
            p = subprocess.run(["netstat", "-ano"], capture_output=True, text=True, check=False)
            out = p.stdout or ""
            pids = set()
            for line in out.splitlines():
                if ":8765" in line and "LISTENING" in line:
                    parts = line.split()
                    if parts:
                        pids.add(parts[-1])
            for pid in pids:
                subprocess.run(["taskkill", "/PID", pid, "/T", "/F"], check=False)
                killed_any = True
        except Exception:
            pass

