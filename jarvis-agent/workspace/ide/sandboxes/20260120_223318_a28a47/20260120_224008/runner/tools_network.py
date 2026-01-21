from __future__ import annotations
from typing import Any, Dict, List, Optional, Tuple
import json
import os
import subprocess
import time
import re
from pathlib import Path
from datetime import datetime


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

# =========================
# Wi-Fi networks (scan) + known profiles
# =========================

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
    out = p.stdout or ""
    profiles: List[str] = []
    for ln in out.splitlines():
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
            current = {"ssid": ssid_val, "bssids": []}
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

        # SSID-level fields
        if key == "network type":
            current["network_type"] = val
            continue
        if key == "authentication":
            current["authentication"] = val
            continue
        if key == "encryption":
            current["encryption"] = val
            continue

        # BSSID-level fields
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

        # Fallback
        if key == "signal" and "signal" not in current:
            current["signal"] = val
            current["signal_percent"] = _parse_signal_percent(val)

    if current:
        networks.append(current)
    return networks


def network_list_wifi_networks(params: Dict[str, Any]) -> Dict[str, Any]:
    """List nearby Wi-Fi SSIDs and mark which ones are already saved on this PC.

    params:
      - include_bssids: bool (default False)
      - max_networks: int (default 30)

    Note:
      - This tool is read-only and does NOT return Wi-Fi passwords.
    """
    if os.name != "nt":
        return {"error": "network.list_wifi_networks is only implemented on Windows."}

    include_bssids = bool(params.get("include_bssids", False))
    max_networks = int(params.get("max_networks", 30))
    max_networks = max(1, min(200, max_networks))

    saved = _netsh_wifi_profiles()
    saved_set = set(saved)

    networks = _netsh_wifi_networks(mode_bssid=True)

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
            n.pop("bssids", None)

    def _sort_key(n: Dict[str, Any]):
        s = n.get("best_signal_percent")
        s_val = s if isinstance(s, int) else -1
        return (-s_val, (n.get("ssid") or "").lower())

    networks.sort(key=_sort_key)
    networks = networks[:max_networks]

    state = _netsh_wifi_details()
    known_count = sum(1 for n in networks if n.get("known"))
    return {
        "result": {
            "connected_ssid": state.get("ssid"),
            "wifi_state": state.get("state"),
            "saved_profiles_count": len(saved),
            "returned_networks": len(networks),
            "known_count": known_count,
            "unknown_count": len(networks) - known_count,
            "networks": networks,
            "note": "known=true means a saved Wi-Fi profile exists on this PC (no passwords are returned).",
        }
    }


# =========================
# Data usage (adapter stats)
# =========================

def _ps_json(script: str) -> Tuple[int, Optional[Any], str]:
    """Run PowerShell and parse JSON output. Returns (exit_code, obj_or_none, err_text)."""
    code, out, err = _run_powershell(script)
    if code != 0:
        return code, None, err or out
    if not out:
        return 0, None, ""
    try:
        return 0, json.loads(out), ""
    except Exception as e:
        return 0, None, f"Failed to parse JSON: {e}; output={out[:200]}"


def network_get_data_usage_total(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get network adapter RX/TX byte counters (since boot) and totals.

    params:
      - include_down_adapters: bool (default False)
    """
    if os.name != "nt":
        return {"error": "network.get_data_usage_total is only implemented on Windows."}

    include_down = bool(params.get("include_down_adapters", False))

    ps = r'''
$stats = Get-NetAdapterStatistics | Select-Object Name, ReceivedBytes, SentBytes
$adapters = Get-NetAdapter | Select-Object Name, Status, InterfaceDescription, MacAddress, LinkSpeed, InterfaceGuid, ifIndex
$result = @()
foreach($s in $stats){
  $a = $adapters | Where-Object { $_.Name -eq $s.Name } | Select-Object -First 1
  if($null -eq $a){ continue }
  $obj = [ordered]@{
    name = $s.Name
    status = $a.Status
    description = $a.InterfaceDescription
    mac = $a.MacAddress
    link_speed = $a.LinkSpeed
    if_index = $a.ifIndex
    received_bytes = [int64]$s.ReceivedBytes
    sent_bytes = [int64]$s.SentBytes
    total_bytes = [int64]$s.ReceivedBytes + [int64]$s.SentBytes
  }
  $result += New-Object psobject -Property $obj
}
$result | ConvertTo-Json -Depth 6 -Compress
'''
    code, obj, err = _ps_json(ps)
    if code != 0 or obj is None:
        return {"error": "Failed to query adapter statistics.", "details": err}

    adapters = obj if isinstance(obj, list) else [obj]
    if not include_down:
        adapters = [a for a in adapters if str(a.get("status", "")).lower() == "up"]

    total_rx = sum(int(a.get("received_bytes", 0) or 0) for a in adapters)
    total_tx = sum(int(a.get("sent_bytes", 0) or 0) for a in adapters)

    state = network_get_state({})
    return {
        "result": {
            "connected_ssid": (state.get("result") or {}).get("ssid"),
            "wifi_enabled": (state.get("result") or {}).get("wifi_enabled"),
            "total_received_bytes": total_rx,
            "total_sent_bytes": total_tx,
            "total_bytes": total_rx + total_tx,
            "adapters": adapters,
            "note": "Counters are since last boot (from Get-NetAdapterStatistics).",
        }
    }


_WIFI_SESSION: Dict[str, Any] = {"ssid": None, "rx0": None, "tx0": None, "started_at": None}


def _get_wifi_adapter_name_ps() -> str:
    ps = r'''
$wifi = Get-NetAdapter | Where-Object { $_.Status -eq "Up" -and ($_.InterfaceDescription -match "Wi-Fi" -or $_.Name -match "Wi-Fi") } | Select-Object -First 1 -ExpandProperty Name
if($null -eq $wifi){ "" } else { $wifi }
'''
    code, out, err = _run_powershell(ps)
    return (out or "").strip()


def network_get_data_usage_current_wifi(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get data usage for the current Wi-Fi connection (session tracked) + adapter totals since boot."""
    if os.name != "nt":
        return {"error": "network.get_data_usage_current_wifi is only implemented on Windows."}

    state = network_get_state({}).get("result") or {}
    ssid = state.get("ssid")

    wifi_name = _get_wifi_adapter_name_ps()
    if not wifi_name:
        return {"error": "Could not find an active Wi-Fi adapter."}

    ps = rf'''
$s = Get-NetAdapterStatistics -Name "{wifi_name}" | Select-Object ReceivedBytes, SentBytes
$s | ConvertTo-Json -Compress
'''
    code, obj, err = _ps_json(ps)
    if code != 0 or obj is None:
        return {"error": "Failed to query Wi-Fi adapter statistics.", "details": err}

    rx = int(obj.get("ReceivedBytes", 0) or 0)
    tx = int(obj.get("SentBytes", 0) or 0)

    if _WIFI_SESSION["ssid"] != ssid or _WIFI_SESSION["rx0"] is None:
        _WIFI_SESSION.update({"ssid": ssid, "rx0": rx, "tx0": tx, "started_at": datetime.now().isoformat()})

    rx0 = int(_WIFI_SESSION["rx0"] or 0)
    tx0 = int(_WIFI_SESSION["tx0"] or 0)

    return {
        "result": {
            "ssid": ssid,
            "wifi_adapter": wifi_name,
            "session_started_at": _WIFI_SESSION["started_at"],
            "session_received_bytes": max(0, rx - rx0),
            "session_sent_bytes": max(0, tx - tx0),
            "session_total_bytes": max(0, rx - rx0) + max(0, tx - tx0),
            "adapter_received_bytes_since_boot": rx,
            "adapter_sent_bytes_since_boot": tx,
            "adapter_total_bytes_since_boot": rx + tx,
            "note": "Session counters reset when SSID changes (tracked in this runner session only).",
        }
    }


# =========================
# Connection + hardware properties
# =========================

def network_get_connection_properties(params: Dict[str, Any]) -> Dict[str, Any]:
    """Return network hardware and connection properties (adapters + IP config + Wi-Fi details)."""
    if os.name != "nt":
        return {"error": "network.get_connection_properties is only implemented on Windows."}

    ps = r'''
$adapters = Get-NetAdapter | Select-Object Name, Status, InterfaceDescription, MacAddress, LinkSpeed, DriverInformation, ifIndex
$ip = Get-NetIPConfiguration | Select-Object InterfaceAlias, IPv4Address, IPv6Address, IPv4DefaultGateway, DNSServer, NetProfile
$result = [ordered]@{
  adapters = $adapters
  ip_configuration = $ip
}
$result | ConvertTo-Json -Depth 8 -Compress
'''
    code, obj, err = _ps_json(ps)
    if code != 0:
        return {"error": "Failed to query connection properties.", "details": err}

    wifi = _netsh_wifi_details()
    return {
        "result": {
            "wifi": wifi,
            "properties": obj,
            "note": "Includes adapters + IP configuration (PowerShell) and Wi-Fi interface state (netsh).",
        }
    }


# =========================
# Mobile Hotspot (tethering) status + toggle
# =========================

def _hotspot_ps(enable: Optional[bool]) -> str:
    # We intentionally DO NOT output hotspot password.
    action = "status"
    if enable is True:
        action = "on"
    elif enable is False:
        action = "off"

    return rf'''
Add-Type -AssemblyName System.Runtime.WindowsRuntime | Out-Null

function Await-AsyncOperation($op) {{
    if($null -eq $op) {{ return $null }}

    # Find generic AsTask<T>(IAsyncOperation<T>) overload
    $ext = [System.WindowsRuntimeSystemExtensions]
    $m = $ext.GetMethods() | Where-Object {{
        $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
    }} | Select-Object -First 1

    if($null -eq $m) {{
        throw "AsTask generic overload not found. Ensure System.Runtime.WindowsRuntime is available."
    }}

    # Prefer the known result type for tethering ops; fallback to runtime generic argument if needed
    $tArg = $null
    try {{
        $tArg = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringOperationResult]
    }} catch {{
        try {{
            $tArg = $op.GetType().GenericTypeArguments | Select-Object -First 1
        }} catch {{
            $tArg = $null
        }}
    }}

    if($null -eq $tArg) {{
        throw "Could not determine async operation result type."
    }}

    $gm = $m.MakeGenericMethod($tArg)
    $task = $gm.Invoke($null, @($op))
    $task.Wait()
    return $task.Result
}}

try {{
    $netInfo = [Windows.Networking.Connectivity.NetworkInformation,Windows.Networking.Connectivity,ContentType=WindowsRuntime]
    $profile = $netInfo::GetInternetConnectionProfile()
    if($null -eq $profile) {{
        $profile = $netInfo::GetConnectionProfiles() | Select-Object -First 1
    }}

    if($null -eq $profile) {{
        $out = [ordered]@{{ supported=$false; error="No connection profile found."; action="{action}" }}
        $out | ConvertTo-Json -Compress
        exit 0
    }}

    $mgrType = [Windows.Networking.NetworkOperators.NetworkOperatorTetheringManager,Windows.Networking.NetworkOperators,ContentType=WindowsRuntime]
    $mgr = $mgrType::CreateFromConnectionProfile($profile)

    $state = $mgr.TetheringOperationalState

    $ssid = $null
    try {{
        $ap = $mgr.GetCurrentAccessPointConfiguration()
        $ssid = $ap.Ssid
    }} catch {{ }}

    if("{action}" -eq "on") {{
        $res = Await-AsyncOperation ($mgr.StartTetheringAsync())
        $state = $mgr.TetheringOperationalState
        $out = [ordered]@{{
            supported=$true
            action="on"
            requested_enabled=$true
            operational_state=[int]$state
            ssid=$ssid
            status=$res.Status
            additional_error=$res.AdditionalErrorMessage
        }}
        $out | ConvertTo-Json -Compress
        exit 0
    }}

    if("{action}" -eq "off") {{
        $res = Await-AsyncOperation ($mgr.StopTetheringAsync())
        $state = $mgr.TetheringOperationalState
        $out = [ordered]@{{
            supported=$true
            action="off"
            requested_enabled=$false
            operational_state=[int]$state
            ssid=$ssid
            status=$res.Status
            additional_error=$res.AdditionalErrorMessage
        }}
        $out | ConvertTo-Json -Compress
        exit 0
    }}

    $out = [ordered]@{{
        supported=$true
        action="status"
        operational_state=[int]$state
        ssid=$ssid
    }}
    $out | ConvertTo-Json -Compress
}} catch {{
    $out = [ordered]@{{ supported=$false; error=$_.Exception.Message; action="{action}" }}
    $out | ConvertTo-Json -Compress
}}
'''



def network_hotspot_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """Get Mobile Hotspot status (on/off + SSID). Does NOT return password."""
    if os.name != "nt":
        return {"error": "network.hotspot_status is only implemented on Windows."}

    code, obj, err = _ps_json(_hotspot_ps(enable=None))
    if code != 0 or obj is None:
        return {"error": "Failed to query hotspot status.", "details": err}

    state_map = {0: "Off", 1: "On", 2: "InTransition"}
    op_state = obj.get("operational_state") if isinstance(obj, dict) else None
    label = state_map.get(int(op_state)) if isinstance(op_state, int) else None

    return {
        "result": {
            "supported": bool(obj.get("supported")) if isinstance(obj, dict) else False,
            "state": label,
            "operational_state": op_state,
            "ssid": obj.get("ssid") if isinstance(obj, dict) else None,
            "note": "Password is not returned. If you need it, open Settings > Network & internet > Mobile hotspot.",
        }
    }


def network_hotspot_toggle(params: Dict[str, Any]) -> Dict[str, Any]:
    """Turn Mobile Hotspot on/off (Windows 10/11) using WinRT tethering APIs.

    params:
      - enabled: bool (required)
      - wait_seconds: int (default 8)  # how long to poll for final state
      - poll_interval_ms: int (default 600)
    """
    if os.name != "nt":
        return {"error": "network.hotspot_toggle is only implemented on Windows."}

    desired = params.get("enabled")
    if desired is None:
        return {"error": "Missing param: enabled (true/false)."}

    desired_bool = bool(desired)
    wait_seconds = int(params.get("wait_seconds", 15))
    poll_interval_ms = int(params.get("poll_interval_ms", 500))

    wait_seconds = max(0, min(20, wait_seconds))
    poll_interval_ms = max(200, min(2000, poll_interval_ms))

    # Kick the requested action
    code, obj, err = _ps_json(_hotspot_ps(enable=desired_bool))
    if code != 0 or obj is None:
        return {
            "result": {
                "supported": False,
                "requested_enabled": desired_bool,
                "action": "open_settings",
                "uri": "ms-settings:network-mobilehotspot",
                "note": "Automatic toggle failed; opening Mobile Hotspot settings instead.",
                "details": err,
            }
        }

    # If it already resolved, return immediately
    op_state = None
    try:
        op_state = int(obj.get("operational_state"))  # 0 off, 1 on, 2 transition
    except Exception:
        op_state = None

    # Poll status until it becomes stable or times out
    if wait_seconds > 0 and op_state == 2:
        deadline = time.time() + wait_seconds
        last_status = None

        while time.time() < deadline:
            time.sleep(poll_interval_ms / 1000.0)

            s_code, s_obj, s_err = _ps_json(_hotspot_ps(enable=None))
            if s_code == 0 and isinstance(s_obj, dict) and s_obj.get("supported") is True:
                last_status = s_obj
                try:
                    s_state = int(s_obj.get("operational_state"))
                except Exception:
                    s_state = None

                if s_state in (0, 1):
                    # Merge stable status into response
                    obj["final_operational_state"] = s_state
                    obj["final_state"] = "On" if s_state == 1 else "Off"
                    obj["final_ssid"] = s_obj.get("ssid")
                    return {"result": obj}

        # Timed out: return best effort
        if isinstance(last_status, dict):
            obj["final_operational_state"] = last_status.get("operational_state")
            obj["final_state"] = "On" if last_status.get("operational_state") == 1 else ("Off" if last_status.get("operational_state") == 0 else "InTransition")
            obj["final_ssid"] = last_status.get("ssid")
            obj["note"] = "Hotspot was still transitioning when timeout was reached."

    return {"result": obj}



