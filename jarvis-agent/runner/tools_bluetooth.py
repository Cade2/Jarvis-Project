from __future__ import annotations
from typing import Any, Dict, Tuple
import os
import json
import subprocess
import time
import inspect


def _debug_impl() -> Dict[str, str]:
    """
    Return proof of which module/file is currently executing.
    """
    try:
        return {
            "file": __file__,
            "module": __name__,
            "function": inspect.stack()[1].function,
        }
    except Exception:
        return {"file": "<unknown>", "module": "<unknown>", "function": "<unknown>"}


def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def bluetooth_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read Bluetooth adapter state using Get-PnpDevice (more reliable than WinRT Radios).
    """
    if os.name != "nt":
        return {
            "error": "bluetooth.get_state is only implemented on Windows right now.",
            "debug_impl": _debug_impl(),
        }

    ps = r"""
    try {
      if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) {
        @{ supported = $false; error = "Get-PnpDevice not available (PnPDevice module missing)" } | ConvertTo-Json -Depth 6
        exit 0
      }

      $all = Get-PnpDevice -Class Bluetooth -ErrorAction Stop |
        Select-Object FriendlyName, InstanceId, Status

      $paired = @($all | Where-Object { $_.InstanceId -like "BTHENUM\*" })
      $adapter = @($all | Where-Object { $_.InstanceId -notlike "BTHENUM\*" } | Select-Object -First 1)

      if (-not $adapter -or $adapter.Count -eq 0) {
        @{
          supported = $false
          adapter_found = $false
          paired_count = $paired.Count
        } | ConvertTo-Json -Depth 6
        exit 0
      }

      $a = $adapter[0]
      $enabled = $true
      if ($a.Status -eq "Disabled") { $enabled = $false }

      @{
        supported = $true
        adapter_found = $true
        adapter_name = $a.FriendlyName
        adapter_instance_id = $a.InstanceId
        adapter_status = $a.Status
        enabled = $enabled
        paired_count = $paired.Count
      } | ConvertTo-Json -Depth 6
    } catch {
      @{ supported = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 6
    }
    """

    code, out, err = _run_powershell(ps)
    if not out:
        return {
            "result": {"supported": False, "error": err or "No PowerShell output"},
            "debug_impl": _debug_impl(),
        }

    try:
        data = json.loads(out)
    except Exception:
        return {
            "result": {"supported": False, "error": "Failed to parse output", "raw": out},
            "debug_impl": _debug_impl(),
        }

    return {"result": data, "debug_impl": _debug_impl()}


def bluetooth_toggle(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enable/disable the Bluetooth adapter via Enable/Disable-PnpDevice (admin often required).
    Verified with before/after state.
    """
    if os.name != "nt":
        return {
            "error": "bluetooth.toggle is only implemented on Windows right now.",
            "debug_impl": _debug_impl(),
        }

    desired = params.get("enabled")
    if desired is None:
        return {"error": "Missing param 'enabled' (true/false).", "debug_impl": _debug_impl()}

    desired = bool(desired)

    before = bluetooth_get_state({})
    bres = before.get("result") or {}
    if not bres.get("supported") or not bres.get("adapter_found"):
        return {
            "result": {"supported": False, "requested_enabled": desired, "before": bres},
            "debug_impl": _debug_impl(),
        }

    instance_id = bres.get("adapter_instance_id")
    if not instance_id:
        return {
            "result": {"supported": False, "requested_enabled": desired, "before": bres, "error": "No adapter_instance_id"},
            "debug_impl": _debug_impl(),
        }

    cmdlet = "Enable-PnpDevice" if desired else "Disable-PnpDevice"

    ps = rf"""
    try {{
      if (-not (Get-Command {cmdlet} -ErrorAction SilentlyContinue)) {{
        @{{
          ok = $false
          error = "{cmdlet} not available"
        }} | ConvertTo-Json -Depth 6
        exit 0
      }}

      {cmdlet} -InstanceId "{instance_id}" -Confirm:$false -ErrorAction Stop | Out-Null
      @{{ ok = $true }} | ConvertTo-Json -Depth 6
    }} catch {{
      @{{ ok = $false; error = $_.Exception.Message }} | ConvertTo-Json -Depth 6
    }}
    """

    code, out, err = _run_powershell(ps)
    time.sleep(0.8)
    after = bluetooth_get_state({})
    ares = after.get("result") or {}

    ok = False
    perr = None
    if out:
        try:
            parsed = json.loads(out)
            ok = bool(parsed.get("ok"))
            perr = parsed.get("error")
        except Exception:
            perr = out

    changed = False
    if bres.get("enabled") is not None and ares.get("enabled") is not None:
        changed = (bool(bres["enabled"]) != bool(ares["enabled"]))

    return {
        "result": {
            "supported": True,
            "requested_enabled": desired,
            "before": bres,
            "after": ares,
            "changed": changed,
            "ps_exit_code": code,
            "ps_error": perr or err or None,
            "note": "If this fails, run Jarvis/runner elevated (Admin).",
        },
        "debug_impl": _debug_impl(),
    }


def bluetooth_list_paired(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    List paired Bluetooth devices using PnP (BTHENUM instances).
    """
    if os.name != "nt":
        return {
            "error": "bluetooth.list_paired is only implemented on Windows right now.",
            "debug_impl": _debug_impl(),
        }

    ps = r"""
    try {
      if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) {
        @{ error = "Get-PnpDevice not available (PnPDevice module missing)" } | ConvertTo-Json -Depth 6
        exit 0
      }

      $dev = @(
        Get-PnpDevice -Class Bluetooth -ErrorAction Stop |
          Where-Object { $_.InstanceId -like "BTHENUM\*" } |
          Select-Object FriendlyName, InstanceId, Status
      )

      if (-not $dev -or $dev.Count -eq 0) {
        "[]"
      } else {
        $dev | ConvertTo-Json -Depth 6
      }
    } catch {
      @{ error = $_.Exception.Message } | ConvertTo-Json -Depth 6
    }
    """

    code, out, err = _run_powershell(ps)
    if not out:
        return {
            "result": {"devices": [], "error": err or "No output"},
            "debug_impl": _debug_impl(),
        }

    try:
        data = json.loads(out)
    except Exception:
        return {
            "result": {"devices": [], "error": "Failed to parse output", "raw": out},
            "debug_impl": _debug_impl(),
        }

    if isinstance(data, dict) and data.get("error"):
        return {
            "result": {"devices": [], "error": data["error"]},
            "debug_impl": _debug_impl(),
        }

    devices = data if isinstance(data, list) else [data]
    devices = [d for d in devices if d and d.get("FriendlyName")]
    return {"result": {"devices": devices}, "debug_impl": _debug_impl()}
