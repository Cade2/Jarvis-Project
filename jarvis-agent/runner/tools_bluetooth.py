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


_PS_AWAIT_HELPERS = r"""
# Load WinRT task helpers (works on most Windows 10/11 installs)
Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue | Out-Null

function Await-AsyncOperation($op) {
  try {
    $t = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeSystemExtensions]::AsTask($op)
  } catch {
    $t = [System.WindowsRuntimeSystemExtensions]::AsTask($op)
  }
  $t.Wait()
  return $t.Result
}

function Await-AsyncAction($op) {
  try {
    $t = [System.Runtime.InteropServices.WindowsRuntime.WindowsRuntimeSystemExtensions]::AsTask($op)
  } catch {
    $t = [System.WindowsRuntimeSystemExtensions]::AsTask($op)
  }
  $t.Wait()
}
"""


def _pnp_state_ps() -> str:
    return r"""
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
    pnp_enabled = $enabled
    paired_count = $paired.Count
  } | ConvertTo-Json -Depth 6
} catch {
  @{ supported = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 6
}
"""


def _winrt_radio_state_ps() -> str:
    # Returns {"supported": bool, "radio_state": "On"/"Off"/...}
    return _PS_AWAIT_HELPERS + r"""
try {
  # Force-load WinRT type
  $null = [Windows.Devices.Radios.Radio, Windows.Devices.Radios, ContentType=WindowsRuntime]

  $radiosOp = [Windows.Devices.Radios.Radio]::GetRadiosAsync()
  $radios = Await-AsyncOperation $radiosOp

  $bt = $radios | Where-Object { $_.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth } | Select-Object -First 1

  if (-not $bt) {
    @{ supported = $false; error = "Bluetooth radio not found via WinRT" } | ConvertTo-Json -Depth 6
    exit 0
  }

  @{
    supported = $true
    radio_state = $bt.State.ToString()
  } | ConvertTo-Json -Depth 6
} catch {
  @{ supported = $false; error = $_.Exception.Message } | ConvertTo-Json -Depth 6
}
"""


def bluetooth_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "bluetooth.get_state is only implemented on Windows right now."}

    # PnP (adapter/device-manager level)
    c1, out1, err1 = _run_powershell(_pnp_state_ps())
    pnp = {}
    if out1:
        try:
            pnp = json.loads(out1)
        except Exception:
            pnp = {"supported": False, "error": "Failed to parse PnP output", "raw": out1}
    else:
        pnp = {"supported": False, "error": err1 or "No PowerShell output (PnP)"}

    # WinRT (Settings/Quick Settings radio level)
    c2, out2, err2 = _run_powershell(_winrt_radio_state_ps())
    radio = {}
    if out2:
        try:
            radio = json.loads(out2)
        except Exception:
            radio = {"supported": False, "error": "Failed to parse WinRT output", "raw": out2}
    else:
        radio = {"supported": False, "error": err2 or "No PowerShell output (WinRT)"}

    # Compute effective_enabled: prefer WinRT radio if available, else PnP
    effective_enabled = None
    if radio.get("supported") and radio.get("radio_state"):
        effective_enabled = (radio["radio_state"].lower() == "on")
    elif pnp.get("supported") and "pnp_enabled" in pnp:
        effective_enabled = bool(pnp["pnp_enabled"])

    return {
        "result": {
            **pnp,
            "winrt_supported": bool(radio.get("supported")),
            "radio_state": radio.get("radio_state"),
            "radio_error": None if radio.get("supported") else radio.get("error"),
            "enabled": effective_enabled,
        }
    }


def bluetooth_toggle(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "bluetooth.toggle is only implemented on Windows right now."}

    desired = params.get("enabled")
    if desired is None:
        return {"error": "Missing param 'enabled' (true/false)."}
    desired = bool(desired)

    before = bluetooth_get_state({}).get("result", {})
    if not before.get("supported") and not before.get("winrt_supported"):
        return {"result": {"supported": False, "requested_enabled": desired, "before": before}}

    # 1) Try WinRT radio toggle first (matches Settings/Quick Settings)
    winrt_ok = False
    winrt_err = None

    if before.get("winrt_supported"):
        target = "On" if desired else "Off"
        ps = _PS_AWAIT_HELPERS + rf"""
try {{
  $null = [Windows.Devices.Radios.Radio, Windows.Devices.Radios, ContentType=WindowsRuntime]
  $radios = Await-AsyncOperation ([Windows.Devices.Radios.Radio]::GetRadiosAsync())
  $bt = $radios | Where-Object {{ $_.Kind -eq [Windows.Devices.Radios.RadioKind]::Bluetooth }} | Select-Object -First 1
  if (-not $bt) {{ throw "Bluetooth radio not found" }}

  $state = [Windows.Devices.Radios.RadioState]::{target}
  Await-AsyncAction ($bt.SetStateAsync($state))
  @{{ ok = $true }} | ConvertTo-Json -Depth 6
}} catch {{
  @{{ ok = $false; error = $_.Exception.Message }} | ConvertTo-Json -Depth 6
}}
"""
        code, out, err = _run_powershell(ps)
        if out:
            try:
                parsed = json.loads(out)
                winrt_ok = bool(parsed.get("ok"))
                winrt_err = parsed.get("error")
            except Exception:
                winrt_ok = False
                winrt_err = out or err
        else:
            winrt_ok = False
            winrt_err = err or "No output (WinRT toggle)"

        time.sleep(0.6)

    after = bluetooth_get_state({}).get("result", {})

    # If WinRT worked and state changed, stop here
    changed = (before.get("enabled") is not None and after.get("enabled") is not None and before["enabled"] != after["enabled"])
    if winrt_ok and changed:
        return {
            "result": {
                "supported": True,
                "method": "winrt_radio",
                "requested_enabled": desired,
                "before": before,
                "after": after,
                "changed": True,
                "ps_error": None,
            }
        }

    # 2) Fallback: try PnP enable/disable (stronger, device-manager level)
    if not before.get("adapter_found") or not before.get("adapter_instance_id"):
        return {
            "result": {
                "supported": True,
                "method": "winrt_radio_failed_no_pnp_fallback",
                "requested_enabled": desired,
                "before": before,
                "after": after,
                "changed": changed,
                "ps_error": winrt_err,
                "note": "WinRT radio toggle failed and no adapter instance id for PnP fallback.",
            }
        }

    instance_id = before["adapter_instance_id"]
    cmdlet = "Enable-PnpDevice" if desired else "Disable-PnpDevice"

    ps2 = rf"""
try {{
  if (-not (Get-Command {cmdlet} -ErrorAction SilentlyContinue)) {{
    @{{ ok = $false; error = "{cmdlet} not available" }} | ConvertTo-Json -Depth 6
    exit 0
  }}
  {cmdlet} -InstanceId "{instance_id}" -Confirm:$false -ErrorAction Stop | Out-Null
  @{{ ok = $true }} | ConvertTo-Json -Depth 6
}} catch {{
  @{{ ok = $false; error = $_.Exception.Message }} | ConvertTo-Json -Depth 6
}}
"""
    code2, out2, err2 = _run_powershell(ps2)
    time.sleep(0.8)
    after2 = bluetooth_get_state({}).get("result", {})

    pnp_ok = False
    pnp_err = None
    if out2:
        try:
            parsed2 = json.loads(out2)
            pnp_ok = bool(parsed2.get("ok"))
            pnp_err = parsed2.get("error")
        except Exception:
            pnp_err = out2
    else:
        pnp_err = err2 or "No output (PnP toggle)"

    changed2 = (before.get("enabled") is not None and after2.get("enabled") is not None and before["enabled"] != after2["enabled"])

    return {
        "result": {
            "supported": True,
            "method": "pnp_fallback" if pnp_ok else "pnp_failed",
            "requested_enabled": desired,
            "before": before,
            "after": after2,
            "changed": changed2,
            "winrt_ok": winrt_ok,
            "winrt_error": winrt_err,
            "ps_exit_code": code2,
            "ps_error": pnp_err or err2 or None,
            "note": "If PnP fails, run runner elevated (Admin). Some drivers refuse disable/enable.",
        }
    }


def bluetooth_list_paired(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    List paired Bluetooth devices by scanning for BTHENUM instance ids across all PnP devices.
    Filters out service-only entries and deduplicates.
    """
    if os.name != "nt":
        return {"error": "bluetooth.list_paired is only implemented on Windows right now."}

    ps = r"""
try {
  if (-not (Get-Command Get-PnpDevice -ErrorAction SilentlyContinue)) {
    @{ error = "Get-PnpDevice not available (PnPDevice module missing)" } | ConvertTo-Json -Depth 6
    exit 0
  }

  $dev = @(
    Get-PnpDevice -PresentOnly -ErrorAction Stop |
      Where-Object { $_.InstanceId -like "BTHENUM\*" } |
      Select-Object FriendlyName, InstanceId, Status, Class
  )

  if (-not $dev -or $dev.Count -eq 0) { "[]" }
  else { $dev | ConvertTo-Json -Depth 6 }
} catch {
  @{ error = $_.Exception.Message } | ConvertTo-Json -Depth 6
}
"""
    code, out, err = _run_powershell(ps)
    if not out:
        return {"result": {"devices": [], "error": err or "No output"}}

    try:
        data = json.loads(out)
    except Exception:
        return {"result": {"devices": [], "error": "Failed to parse output", "raw": out}}

    if isinstance(data, dict) and data.get("error"):
        return {"result": {"devices": [], "error": data["error"]}}

    devices = data if isinstance(data, list) else [data]
    devices = [d for d in devices if d and d.get("FriendlyName")]

    # Filter out service entries
    bad_words = ("avrcp transport", "audio gateway service", "hands-free", "headset")
    devices = [
        d for d in devices
        if not any(w in d["FriendlyName"].lower() for w in bad_words)
    ]

    # Deduplicate by FriendlyName
    seen = set()
    clean = []
    for d in devices:
        name = d["FriendlyName"].strip().lower()
        if name in seen:
            continue
        seen.add(name)
        clean.append(d)

    return {"result": {"devices": clean}}

def bluetooth_connect_paired(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    v0: Best-effort "connect" placeholder.

    Real connection requires UI Automation (UIA) or deeper WinRT APIs.
    For now we open the Bluetooth Settings page so the user can click Connect.

    params:
      - name: optional device name to connect
    """
    if os.name != "nt":
        return {"error": "bluetooth.connect_paired is only implemented on Windows right now."}

    name = (params.get("name") or "").strip()

    # Open Bluetooth settings page
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:bluetooth"], shell=False)
        return {
            "result": {
                "supported": False,
                "action": "open_settings",
                "uri": "ms-settings:bluetooth",
                "requested_device": name or None,
                "note": "UIA-based connect will be added later. For now, Settings is opened to connect manually.",
            }
        }
    except Exception as e:
        return {"result": {"supported": False, "error": str(e), "requested_device": name or None}}


