from __future__ import annotations
from typing import Dict, Any
import subprocess
import tempfile
from pathlib import Path
from datetime import datetime

def _run_cmd(args: list[str]) -> Dict[str, Any]:
    try:
        cp = subprocess.run(args, capture_output=True, text=True, shell=False)
        return {
            "ok": cp.returncode == 0,
            "stdout": (cp.stdout or "").strip(),
            "stderr": (cp.stderr or "").strip(),
            "code": cp.returncode,
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "code": -1}

def power_srum_report(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate SRUM report (CSV or XML) via: powercfg /srumutil
    Returns the path to the created file.
    """
    fmt = (params.get("format") or "csv").strip().lower()
    if fmt not in ("csv", "xml"):
        return {"result": {"ok": False, "error": "format must be 'csv' or 'xml'"}}

    out_dir = Path(tempfile.gettempdir()) / "jarvis_power_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"srum_report_{ts}.{fmt}"

    args = ["powercfg", "/srumutil", "/output", str(out_file)]
    args.append("/csv" if fmt == "csv" else "/xml")

    res = _run_cmd(args)
    if not res["ok"]:
        # Common issue: needs admin on some systems
        return {
            "result": {
                "ok": False,
                "error": res["stderr"] or res["stdout"] or "powercfg /srumutil failed",
                "code": res["code"],
            }
        }

    return {"result": {"ok": True, "path": str(out_file), "format": fmt}}
