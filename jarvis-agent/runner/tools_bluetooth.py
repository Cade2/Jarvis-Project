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

def _ps_json(script: str) -> Tuple[int, Any, str]:
    code, out, err = _run_powershell(script)
    if code != 0:
        return code, None, err or out

    if not out:
        return 0, None, ""

    try:
        return 0, json.loads(out), ""
    except Exception as e:
        snippet = out[:400].replace("\n", "\\n")
        return 0, None, f"JSON parse failed: {e}; output_snippet={snippet}"



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
    Best-effort connect/pair for Bluetooth devices (Windows).

    What it does:
      - Finds device by name via DeviceInformation (Bluetooth Classic + BLE)
      - If not paired and allow_pair=True, attempts PairAsync (may prompt UI)
      - Attempts a "connect" by creating BluetoothLEDevice/BluetoothDevice and querying services
      - Falls back to opening Bluetooth settings if blocked

    params:
      - name: str (required)
      - allow_pair: bool (default True)
      - address: optional bluetooth address like "aa:bb:cc:dd:ee:ff" (BLE shortcut)
    """
    if os.name != "nt":
        return {"error": "bluetooth.connect_paired is only implemented on Windows right now."}

    name = (params.get("name") or "").strip()
    allow_pair = bool(params.get("allow_pair", True))
    address = (params.get("address") or "").strip()

    if not name and not address:
        return {"error": "Provide 'name' (e.g. AirPods) or 'address' (aa:bb:cc:dd:ee:ff)."}

    # Escape for PowerShell single-quoted strings
    name_ps = name.replace("'", "''")

    # Optional address -> UInt64 conversion in PS
    addr_ps = address.replace("'", "''")

    ps = _PS_AWAIT_HELPERS + rf"""
try {{
  Add-Type -AssemblyName System.Runtime.WindowsRuntime -ErrorAction SilentlyContinue | Out-Null

  function Await-Generic($op) {{
    if($null -eq $op) {{ return $null }}
    $ext = [System.WindowsRuntimeSystemExtensions]
    $m = $ext.GetMethods() | Where-Object {{
      $_.Name -eq "AsTask" -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1
    }} | Select-Object -First 1
    if($null -eq $m) {{ throw "Generic AsTask<T> not found" }}
    $tArg = $op.GetType().GenericTypeArguments | Select-Object -First 1
    $gm = $m.MakeGenericMethod($tArg)
    $task = $gm.Invoke($null, @($op))
    $task.Wait()
    return $task.Result
  }}

  $null = [Windows.Devices.Enumeration.DeviceInformation, Windows.Devices.Enumeration, ContentType=WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.BluetoothLEDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.BluetoothDevice, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]

  function AddrToUlong([string]$mac) {{
    if([string]::IsNullOrWhiteSpace($mac)) {{ return $null }}
    $hex = ($mac -replace "[:\-]","").ToUpper()
    if($hex.Length -ne 12) {{ return $null }}
    return [Convert]::ToUInt64($hex, 16)
  }}

  # If address is provided, try BLE direct connect first
  $addrStr = '{addr_ps}'
  if(-not [string]::IsNullOrWhiteSpace($addrStr)) {{
    $u = AddrToUlong $addrStr
    if($u -ne $null) {{
      try {{
        $ble = Await-Generic ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromBluetoothAddressAsync($u))
        if($ble) {{
          $gatt = Await-Generic ($ble.GetGattServicesAsync())
          @{{ supported=$true; mode="address"; address=$addrStr; ble_gatt_status=$gatt.Status.ToString(); note="Address connect is BLE-only." }} | ConvertTo-Json -Depth 8 -Compress
          exit 0
        }}
      }} catch {{
        @{{ supported=$false; mode="address"; address=$addrStr; error=$_.Exception.Message }} | ConvertTo-Json -Depth 8 -Compress
        exit 0
      }}
    }}
  }}

  $target = '{name_ps}'.ToLower()
  $btClassic = "{{e0cbf06c-cd8b-4647-bb8a-263b43f0f974}}"
  $btLe = "{{bb7bb05e-5972-42b5-94fc-76eaa7084d49}}"

  # AQS selector for both classic + LE Association Endpoints
  $aqs = "(System.Devices.Aep.ProtocolId:=""$btClassic"") OR (System.Devices.Aep.ProtocolId:=""$btLe"")"

  $props = @(
    "System.ItemNameDisplay",
    "System.Devices.Aep.IsPaired",
    "System.Devices.Aep.IsConnected",
    "System.Devices.Aep.DeviceAddress",
    "System.Devices.Aep.ProtocolId"
  )

  $devs = Await-Generic ([Windows.Devices.Enumeration.DeviceInformation]::FindAllAsync($aqs, $props))
  $matches = @($devs | Where-Object {{ $_.Name -and $_.Name.ToLower().Contains($target) }})

  if($matches.Count -eq 0) {{
    @{{ supported=$true; matched_count=0; note="No device matched by name. Ensure device is on and discoverable (pairing mode)."; action="open_settings"; uri="ms-settings:bluetooth" }} | ConvertTo-Json -Depth 8 -Compress
    exit 0
  }}

  # Choose best match: exact name > paired > first
  $sel = $matches | Where-Object {{ $_.Name.ToLower() -eq $target }} | Select-Object -First 1
  if(-not $sel) {{
    $sel = $matches | Where-Object {{ $_.Pairing.IsPaired }} | Select-Object -First 1
  }}
  if(-not $sel) {{ $sel = $matches | Select-Object -First 1 }}

  $pair_status = $null
  $paired_before = [bool]$sel.Pairing.IsPaired

  if((-not $paired_before) -and {str(allow_pair).lower()}) {{
    try {{
      $pairRes = Await-Generic ($sel.Pairing.PairAsync())
      $pair_status = $pairRes.Status.ToString()
    }} catch {{
      $pair_status = "PairAsyncFailed: " + $_.Exception.Message
    }}
  }}

  $paired_after = [bool]$sel.Pairing.IsPaired
  $is_connected_prop = $sel.Properties["System.Devices.Aep.IsConnected"]

  # Try BLE connect (GATT)
  $ble_gatt_status = $null
  $ble_error = $null
  try {{
    $ble = Await-Generic ([Windows.Devices.Bluetooth.BluetoothLEDevice]::FromIdAsync($sel.Id))
    if($ble) {{
      $gatt = Await-Generic ($ble.GetGattServicesAsync())
      $ble_gatt_status = $gatt.Status.ToString()
    }}
  }} catch {{
    $ble_error = $_.Exception.Message
  }}

  # Try Classic connect (RFCOMM services)
  $rfcomm_status = $null
  $classic_error = $null
  try {{
    $bt = Await-Generic ([Windows.Devices.Bluetooth.BluetoothDevice]::FromIdAsync($sel.Id))
    if($bt) {{
      $rf = Await-Generic ($bt.GetRfcommServicesAsync())
      $rfcomm_status = $rf.Status.ToString()
    }}
  }} catch {{
    $classic_error = $_.Exception.Message
  }}

  @{{ 
    supported=$true
    matched_count=$matches.Count
    selected_name=$sel.Name
    paired_before=$paired_before
    paired_after=$paired_after
    pair_status=$pair_status
    is_connected_property=$is_connected_prop
    ble_gatt_status=$ble_gatt_status
    ble_error=$ble_error
    rfcomm_status=$rfcomm_status
    classic_error=$classic_error
    note="Best-effort: audio devices may only 'connect' when an app starts audio. If needed, use Settings fallback."
  }} | ConvertTo-Json -Depth 10 -Compress

}} catch {{
  @{{ supported=$false; error=$_.Exception.Message; action="open_settings"; uri="ms-settings:bluetooth" }} | ConvertTo-Json -Depth 8 -Compress
}}
"""

    try:
        code, out, err = _run_powershell(ps)
        if code != 0:
            raise RuntimeError(err or out or f"PowerShell exited with {code}")

        if not out:
            # Fallback to settings
            subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:bluetooth"], shell=False)
            return {"result": {"supported": False, "action": "open_settings", "uri": "ms-settings:bluetooth", "note": "No output from WinRT connect attempt."}}

        try:
            obj = json.loads(out)
        except Exception:
            obj = {"supported": False, "error": "Failed to parse PowerShell JSON", "raw": out[:400]}

        # If tool recommends settings, open it automatically
        if isinstance(obj, dict) and obj.get("action") == "open_settings":
            try:
                subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:bluetooth"], shell=False)
            except Exception:
                pass

        return {"result": obj}

    except Exception as e:
        # Last resort: open settings
        try:
            subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:bluetooth"], shell=False)
        except Exception:
            pass
        return {"result": {"supported": False, "error": str(e), "action": "open_settings", "uri": "ms-settings:bluetooth", "requested_device": name or None}}


def _ble_scan_ps(duration_ms: int, active: bool) -> str:
    mode = "Active" if active else "Passive"
    return _PS_AWAIT_HELPERS + rf"""
try {{
  $null = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
  $null = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode, Windows.Devices.Bluetooth, ContentType=WindowsRuntime]
  $null = [Windows.Foundation.TypedEventHandler`2, Windows.Foundation, ContentType=WindowsRuntime]

  function Format-BTAddress([UInt64]$addr) {{
    $hex = "{{0:X12}}" -f $addr
    return ($hex -replace '(.{{2}})(?!$)','$1:').ToLower()
  }}

  $script:seen = @{{}}  # address -> object

  $watcher = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher]::new()
  $watcher.ScanningMode = [Windows.Devices.Bluetooth.Advertisement.BluetoothLEScanningMode]::{mode}

  # Typed event handler to avoid Register-ObjectEvent scope issues
  $handler = [Windows.Foundation.TypedEventHandler[
      Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementWatcher,
      Windows.Devices.Bluetooth.Advertisement.BluetoothLEAdvertisementReceivedEventArgs
    ]] {{
      param($sender, $args)

      $addr = Format-BTAddress $args.BluetoothAddress
      $name = $args.Advertisement.LocalName
      $rssi = $args.RawSignalStrengthInDBm

      if (-not $script:seen.ContainsKey($addr)) {{
        $script:seen[$addr] = [ordered]@{{
          name = $name
          address = $addr
          rssi_dbm = $rssi
          first_seen = (Get-Date).ToString("o")
          last_seen = (Get-Date).ToString("o")
          hits = 1
        }}
      }} else {{
        $script:seen[$addr].last_seen = (Get-Date).ToString("o")
        $script:seen[$addr].hits = [int]$script:seen[$addr].hits + 1
        if (-not $script:seen[$addr].name -and $name) {{ $script:seen[$addr].name = $name }}
        $script:seen[$addr].rssi_dbm = $rssi
      }}
  }}

  $token = $watcher.add_Received($handler)
  $watcher.Start()
  Start-Sleep -Milliseconds {duration_ms}
  $watcher.Stop()
  $watcher.remove_Received($token) | Out-Null


  @($script:seen.Values) | ConvertTo-Json -Depth 8 -Compress
}} catch {{
  @{{ error = $_.Exception.Message }} | ConvertTo-Json -Depth 6 -Compress
}}
"""



def bluetooth_scan_nearby(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Scan for nearby Bluetooth LE advertising devices (paired or not).
    """
    try:
        if os.name != "nt":
            return {"result": {"devices": [], "error": "Windows only"}}

        duration_seconds = int(params.get("duration_seconds", 6))
        duration_seconds = max(1, min(20, duration_seconds))
        active_scan = bool(params.get("active_scan", True))
        max_devices = int(params.get("max_devices", 40))
        max_devices = max(1, min(200, max_devices))

        code, obj, err = _ps_json(_ble_scan_ps(duration_seconds * 1000, active_scan))
        if code != 0:
            return {"result": {"devices": [], "error": err or "PowerShell scan failed"}}
        
        if obj is None:
            return {
                "result": {
                    "devices": [],
                    "returned": 0,
                    "note": "No BLE advertisements were captured during the scan window (this can be normal).",
                }
            }


        if isinstance(obj, dict) and obj.get("error"):
            return {"result": {"devices": [], "error": obj["error"]}}

        devices = obj if isinstance(obj, list) else [obj]

        # Best-effort paired flag by name match
        paired = bluetooth_list_paired({}).get("result", {}).get("devices", [])
        paired_names = {str(d.get("FriendlyName", "")).strip().lower() for d in paired if d.get("FriendlyName")}

        for d in devices:
            nm = (d.get("name") or "").strip().lower()
            d["paired_known"] = bool(nm and nm in paired_names)

        def _sort_key(x: Dict[str, Any]):
            rssi = x.get("rssi_dbm")
            rssi_val = int(rssi) if isinstance(rssi, (int, float)) else -999
            return (-rssi_val, (x.get("name") or "").lower())

        devices.sort(key=_sort_key)
        devices = devices[:max_devices]

        return {
            "result": {
                "duration_seconds": duration_seconds,
                "active_scan": active_scan,
                "returned": len(devices),
                "devices": devices,
                "note": "BLE advertising scan. Some classic devices may not appear unless discoverable.",
            }
        }
    except Exception as e:
        # Never let runner throw a 500
        return {"result": {"devices": [], "error": f"Unhandled error: {e}"}}


