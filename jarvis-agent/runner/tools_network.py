from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import subprocess
import time
import re


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

# -------------------------
# Wi-Fi scan (read-only)
# -------------------------

_RE_PROFILE = re.compile(r"^\s*All\s+User\s+Profile\s*:\s*(.+?)\s*$", re.I)
_RE_SSID = re.compile(r"^\s*SSID\s+(\d+)\s*:\s*(.*)$", re.I)
_RE_BSSID = re.compile(r"^\s*BSSID\s+(\d+)\s*:\s*(.+?)\s*$", re.I)


def _netsh_wifi_profiles() -> List[str]:
    """Return saved Wi-Fi profile names (SSIDs) via `netsh wlan show profiles`."""
    if os.name != "nt":
        return []

    p = subprocess.run(
        ["netsh", "wlan", "show", "profiles"],
        capture_output=True,
        text=True,
        shell=False,
    )
    text_out = p.stdout or ""
    profiles: List[str] = []
    for ln in text_out.splitlines():
        m = _RE_PROFILE.match(ln)
        if m:
            profiles.append(m.group(1).strip())
    return profiles


def _parse_signal_percent(s: str) -> Optional[int]:
    if not s:
        return None
    m = re.search(r"(\d{1,3})\s*%", s)
    if not m:
        return None
    try:
        v = int(m.group(1))
        return max(0, min(100, v))
    except Exception:
        return None


def _netsh_wifi_networks(mode_bssid: bool = True) -> List[Dict[str, Any]]:
    """Parse `netsh wlan show networks` output into structured data."""
    if os.name != "nt":
        return []

    args = ["netsh", "wlan", "show", "networks"]
    if mode_bssid:
        args += ["mode=bssid"]

    p = subprocess.run(args, capture_output=True, text=True, shell=False)
    out = p.stdout or ""

    networks: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    current_bssid: Optional[Dict[str, Any]] = None

    for raw in out.splitlines():
        line = raw.rstrip("\n")

        m_ssid = _RE_SSID.match(line)
        if m_ssid:
            if current:
                networks.append(current)
            ssid_val = (m_ssid.group(2) or "").strip()
            current = {
                "ssid": ssid_val,
                "bssids": [],
            }
            current_bssid = None
            continue

        if current is None:
            continue

        m_b = _RE_BSSID.match(line)
        if m_b:
            current_bssid = {"bssid": m_b.group(2).strip()}
            current["bssids"].append(current_bssid)
            continue

        if ":" not in line:
            continue

        k, v = line.split(":", 1)
        key = k.strip().lower()
        val = v.strip()

        # Network-level fields
        if key == "network type":
            current["network_type"] = val
            continue
        if key == "authentication":
            current["authentication"] = val
            continue
        if key == "encryption":
            current["encryption"] = val
            continue

        # BSSID-level fields (when present)
        if current_bssid is not None:
            if key == "signal":
                current_bssid["signal"] = val
                current_bssid["signal_percent"] = _parse_signal_percent(val)
                continue
            if key == "radio type":
                current_bssid["radio_type"] = val
                continue
            if key == "channel":
                try:
                    current_bssid["channel"] = int(re.sub(r"[^0-9]", "", val) or "0")
                except Exception:
                    current_bssid["channel"] = val
                continue

        # Fallback signal (some systems report a top-level signal)
        if key == "signal" and "signal" not in current:
            current["signal"] = val
            current["signal_percent"] = _parse_signal_percent(val)

    if current:
        networks.append(current)

    return networks


def network_list_wifi_networks(params: Dict[str, Any]) -> Dict[str, Any]:
    """List nearby Wi-Fi SSIDs and mark which ones are already saved on this PC.

    params:
      - include_bssids: bool (default False)  # include per-BSSID details (channel, radio_type, signal)
      - max_networks: int (default 30)        # cap returned networks after sorting by signal

    Security note:
      - This tool is read-only and **does not return Wi-Fi passwords**.
    """
    if os.name != "nt":
        return {"error": "network.list_wifi_networks is only implemented on Windows right now."}

    include_bssids = bool(params.get("include_bssids", False))
    max_networks = int(params.get("max_networks", 30))
    max_networks = max(1, min(200, max_networks))

    saved = _netsh_wifi_profiles()
    saved_set = set(saved)

    networks = _netsh_wifi_networks(mode_bssid=True)

    # compute a single "best" signal per SSID from BSSIDs
    for n in networks:
        best = None
        for b in n.get("bssids", []) or []:
            sp = b.get("signal_percent")
            if isinstance(sp, int):
                best = sp if best is None else max(best, sp)

        if best is not None:
            n["best_signal_percent"] = best
        elif isinstance(n.get("signal_percent"), int):
            n["best_signal_percent"] = n["signal_percent"]
        else:
            n["best_signal_percent"] = None

        ssid = (n.get("ssid") or "").strip()
        n["known"] = bool(ssid and ssid in saved_set)

        if not include_bssids:
            # keep output compact
            n.pop("bssids", None)

    # sort by signal desc, then ssid
    def _sort_key(n: Dict[str, Any]):
        s = n.get("best_signal_percent")
        s_val = s if isinstance(s, int) else -1
        return (-s_val, (n.get("ssid") or "").lower())

    networks.sort(key=_sort_key)
    networks = networks[:max_networks]

    known_count = sum(1 for n in networks if n.get("known"))
    unknown_count = len(networks) - known_count

    state = _netsh_wifi_details()

    return {
        "result": {
            "connected_ssid": state.get("ssid"),
            "wifi_state": state.get("state"),
            "saved_profiles_count": len(saved),
            "returned_networks": len(networks),
            "known_count": known_count,
            "unknown_count": unknown_count,
            "networks": networks,
            "note": "Passwords are not returned. Known=true means a saved Wi-Fi profile exists on this PC.",
        }
    }

