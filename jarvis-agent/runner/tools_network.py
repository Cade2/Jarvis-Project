from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import subprocess
import time


def _run_powershell(script: str) -> Tuple[int, str, str]:
    """Run a PowerShell one-liner and return (code, stdout, stderr)."""
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _get_adapters() -> List[Dict[str, Any]]:
    """
    Return adapter list from Get-NetAdapter (as JSON).
    Works best on Windows.
    """
    code, out, err = _run_powershell(
        "Get-NetAdapter | Select-Object Name,InterfaceDescription,Status,IfIndex | ConvertTo-Json -Depth 3"
    )
    if code != 0 or not out:
        return []

    try:
        data = json.loads(out)
        if isinstance(data, dict):
            return [data]
        if isinstance(data, list):
            return data
    except Exception:
        return []

    return []


def _is_wifi_adapter(a: Dict[str, Any]) -> bool:
    desc = (a.get("InterfaceDescription") or "").lower()
    name = (a.get("Name") or "").lower()
    hints = ["wi-fi", "wifi", "wireless", "wlan", "802.11"]
    return any(h in desc for h in hints) or any(h in name for h in hints)


def _pick_wifi_adapter(adapters: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    wifi = [a for a in adapters if _is_wifi_adapter(a)]
    if not wifi:
        return None

    # Prefer one that is Up first
    for a in wifi:
        if (a.get("Status") or "").lower() == "up":
            return a

    return wifi[0]


def _netsh_wifi_details() -> Dict[str, Any]:
    """
    Best-effort: parse `netsh wlan show interfaces` for SSID + state.
    """
    if os.name != "nt":
        return {}

    p = subprocess.run(
        ["netsh", "wlan", "show", "interfaces"],
        capture_output=True,
        text=True,
        shell=False,
    )
    text = (p.stdout or "")
    lower = text.lower()

    # default values
    state = None
    ssid = None
    signal = None

    for line in text.splitlines():
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip().lower()
        v = v.strip()

        # "State : connected"
        if k == "state":
            state = v.lower()

        # "SSID : BrinkHome" (ignore BSSID)
        if k == "ssid" and "bssid" not in lower:
            if v and v.lower() != "":  # sometimes blank
                ssid = v

        # "Signal : 88%"
        if k == "signal":
            signal = v

    return {"state": state, "ssid": ssid, "signal": signal}


def network_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read-only network state (focus: Wi-Fi) + basic verification fields.
    """
    if os.name != "nt":
        return {"error": "network.get_state is only implemented on Windows right now."}

    adapters = _get_adapters()
    wifi = _pick_wifi_adapter(adapters)

    wifi_found = wifi is not None
    wifi_name = wifi.get("Name") if wifi_found else None
    wifi_status = wifi.get("Status") if wifi_found else None

    # Treat Disabled as disabled, everything else as enabled-ish
    wifi_enabled = bool(wifi_found and (wifi_status or "").lower() != "disabled")

    details = _netsh_wifi_details()
    wifi_connected = (details.get("state") == "connected") if wifi_found else False

    return {
        "result": {
            "wifi_found": wifi_found,
            "wifi_name": wifi_name,
            "wifi_status": wifi_status,
            "wifi_enabled": wifi_enabled,
            "wifi_connected": wifi_connected,
            "ssid": details.get("ssid"),
            "signal": details.get("signal"),
        }
    }


def network_toggle_wifi(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Enable/disable Wi-Fi adapter with before/after verification.
    params:
      - enabled: bool
    NOTE: This may require admin privileges depending on system policy.
    """
    if os.name != "nt":
        return {"error": "network.toggle_wifi is only implemented on Windows right now."}

    desired = params.get("enabled")
    if desired is None:
        return {"error": "Missing param 'enabled' (true/false)."}

    desired = bool(desired)

    before = network_get_state({})
    if before.get("error"):
        return before

    if not before["result"].get("wifi_found"):
        return {"error": "No Wi-Fi adapter detected on this device."}

    wifi_name = before["result"]["wifi_name"]

    # Use PowerShell NetAdapter cmdlets (more reliable than netsh for toggling)
    if desired:
        ps = f'Enable-NetAdapter -Name "{wifi_name}" -Confirm:$false'
    else:
        ps = f'Disable-NetAdapter -Name "{wifi_name}" -Confirm:$false'

    code, out, err = _run_powershell(ps)

    # Give Windows a moment to update adapter status
    time.sleep(1.0)
    after = network_get_state({})

    changed = False
    if "result" in before and "result" in after:
        changed = before["result"].get("wifi_enabled") != after["result"].get("wifi_enabled")

    return {
        "result": {
            "requested_enabled": desired,
            "before": before["result"],
            "after": after.get("result"),
            "changed": changed,
            "ps_exit_code": code,
            "ps_stdout": out,
            "ps_stderr": err,
            "note": "If ps_exit_code != 0, try running the terminal as Administrator.",
        }
    }


def network_toggle_airplane_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Airplane Mode toggle is unreliable via CLI on Win11 without deeper APIs/UIA.
    For now: we open the Airplane Mode settings page and return a guided result.

    params:
      - enabled: bool (optional)
    """
    # We reuse Settings deep-link behavior by returning a suggested URI.
    desired = params.get("enabled")
    return {
        "result": {
            "supported": False,
            "requested_enabled": desired,
            "action": "open_settings",
            "uri": "ms-settings:network-airplanemode",
            "note": "Direct airplane-mode toggling will be added later via a dedicated Windows API or UIA (gated by policy).",
        }
    }
