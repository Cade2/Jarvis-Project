from __future__ import annotations
from typing import Any, Dict, Tuple
import os
import json
import subprocess
import time


def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def display_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read current brightness using WMI (works on many laptops/internal displays).
    On some desktops/external monitors this may not be supported.
    """
    if os.name != "nt":
        return {"error": "display.get_state is only implemented on Windows right now."}

    ps = (
        "try { "
        "$b = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness -ErrorAction Stop "
        "| Select-Object InstanceName, CurrentBrightness "
        "| ConvertTo-Json -Depth 3; "
        "Write-Output $b "
        "} catch { "
        "Write-Output (ConvertTo-Json @{ supported=$false; error=$_.Exception.Message }) "
        "}"
    )

    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"supported": False, "error": err or "No output from PowerShell."}}

    try:
        data = json.loads(out)
    except Exception:
        return {
            "result": {
                "supported": False,
                "error": "Failed to parse PowerShell output.",
                "raw": out,
            }
        }

    # If our catch block returned {supported:false,...}
    if isinstance(data, dict) and data.get("supported") is False:
        return {"result": {"supported": False, "error": data.get("error", "Not supported")}}

    # If multiple monitors, PS returns a list
    if isinstance(data, list) and data:
        first = data[0]
    elif isinstance(data, dict):
        first = data
    else:
        first = {}

    brightness = first.get("CurrentBrightness", None)
    instance = first.get("InstanceName", None)

    supported = brightness is not None
    return {
        "result": {
            "supported": supported,
            "brightness": int(brightness) if supported else None,
            "instance": instance,
        }
    }


def display_set_brightness(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set brightness (0-100) using WMI method WmiSetBrightness.
    Includes before/after verification.
    """
    if os.name != "nt":
        return {"error": "display.set_brightness is only implemented on Windows right now."}

    level = params.get("level")
    if level is None:
        return {"error": "Missing param 'level' (0-100)."}

    try:
        level_int = int(level)
    except Exception:
        return {"error": "Param 'level' must be an integer 0-100."}

    # clamp
    level_int = max(0, min(100, level_int))

    before = display_get_state({})
    if before.get("error"):
        return before

    if not before["result"].get("supported"):
        return {
            "result": {
                "supported": False,
                "requested_level": level_int,
                "before": before["result"],
                "note": "Brightness control not supported via WMI on this display. Use Settings (ms-settings:display) or vendor/DDC tools.",
            }
        }

    ps = (
        "try { "
        f"$lvl = {level_int}; "
        "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop; "
        "Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{ Timeout = 1; Brightness = [byte]$lvl } "
        "| Out-Null; "
        "Write-Output (ConvertTo-Json @{ ok=$true }) "
        "} catch { "
        "Write-Output (ConvertTo-Json @{ ok=$false; error=$_.Exception.Message }) "
        "}"
    )

    code, out, err = _run_powershell(ps)

    # allow time for the OS to update state
    time.sleep(0.7)
    after = display_get_state({})

    ok = False
    ps_error = None
    try:
        resp = json.loads(out) if out else {}
        ok = bool(resp.get("ok"))
        ps_error = resp.get("error")
    except Exception:
        ok = (code == 0)

    changed = False
    if "result" in after and after["result"].get("supported"):
        changed = before["result"].get("brightness") != after["result"].get("brightness")

    return {
        "result": {
            "supported": True,
            "requested_level": level_int,
            "before": before["result"],
            "after": after.get("result"),
            "changed": changed,
            "ps_exit_code": code,
            "ps_error": ps_error or err or None,
            "note": "If this fails, try running as Administrator or note that external monitors often don't support WMI brightness.",
        }
    }
