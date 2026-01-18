from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple, Union

import json
import os
import subprocess
import time


# NOTE:
# - Brightness: WMI (works mainly for internal laptop panels).
# - Resolution / refresh / orientation: Win32 API (ChangeDisplaySettingsEx).
# - Multi-display modes: DisplaySwitch.exe
# - Scale: registry (usually needs sign-out/in)
# - Night light / HDR: open Settings (direct toggles vary by Windows build)


def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


# ------------------------
# Brightness (existing)
# ------------------------

def display_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read current brightness using WMI (works on many laptops/internal displays).
    Also returns basic connected display mode info (resolution/refresh/orientation).
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
        brightness_result = {"supported": False, "error": err or "No output from PowerShell."}
    else:
        try:
            data = json.loads(out)
            if isinstance(data, dict) and data.get("supported") is False:
                brightness_result = {"supported": False, "error": data.get("error", "Not supported")}
            else:
                first = data[0] if isinstance(data, list) and data else (data if isinstance(data, dict) else {})
                brightness = first.get("CurrentBrightness", None)
                instance = first.get("InstanceName", None)
                supported = brightness is not None
                brightness_result = {
                    "supported": supported,
                    "brightness": int(brightness) if supported else None,
                    "instance": instance,
                }
        except Exception:
            brightness_result = {
                "supported": False,
                "error": "Failed to parse PowerShell output.",
                "raw": out,
            }

    # Add connected display info (resolution/refresh/orientation)
    displays: List[Dict[str, Any]] = []
    try:
        h = _ctypes_display_helpers()
        displays = h["enum_displays"]()
    except Exception:
        displays = []

    primary = next((d for d in displays if d.get("primary")), None) if displays else None

    return {
        "result": {
            **brightness_result,
            "display_count": len(displays),
            "primary_display": primary,
            "displays": displays,
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


# -----------------------------
# Advanced display tooling (MK2)
# -----------------------------

def _require_windows(tool: str) -> Optional[Dict[str, Any]]:
    if os.name != "nt":
        return {"error": f"{tool} is only implemented on Windows right now."}
    return None


def _ctypes_display_helpers():
    """Win32 display helpers: list displays + change mode."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)

    CCHDEVICENAME = 32
    CCHFORMNAME = 32

    # DISPLAY_DEVICE.StateFlags
    DISPLAY_DEVICE_ATTACHED_TO_DESKTOP = 0x00000001
    DISPLAY_DEVICE_PRIMARY_DEVICE = 0x00000004

    ENUM_CURRENT_SETTINGS = -1

    # DEVMODE dmFields bits
    DM_PELSWIDTH = 0x00080000
    DM_PELSHEIGHT = 0x00100000
    DM_DISPLAYFREQUENCY = 0x00400000
    DM_DISPLAYORIENTATION = 0x00000080

    # ChangeDisplaySettingsEx return codes
    DISP_CHANGE_SUCCESSFUL = 0

    # Orientation constants
    DMDO_DEFAULT = 0
    DMDO_90 = 1
    DMDO_180 = 2
    DMDO_270 = 3

    class POINTL(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class DISPLAY_DEVICEW(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("DeviceName", wintypes.WCHAR * 32),
            ("DeviceString", wintypes.WCHAR * 128),
            ("StateFlags", wintypes.DWORD),
            ("DeviceID", wintypes.WCHAR * 128),
            ("DeviceKey", wintypes.WCHAR * 128),
        ]

    # Printer struct (part of DEVMODE union) – kept for correct layout
    class _PRINTER_FIELDS(ctypes.Structure):
        _fields_ = [
            ("dmOrientation", wintypes.SHORT),
            ("dmPaperSize", wintypes.SHORT),
            ("dmPaperLength", wintypes.SHORT),
            ("dmPaperWidth", wintypes.SHORT),
            ("dmScale", wintypes.SHORT),
            ("dmCopies", wintypes.SHORT),
            ("dmDefaultSource", wintypes.SHORT),
            ("dmPrintQuality", wintypes.SHORT),
        ]

    # Display struct (part of DEVMODE union)
    class _DISPLAY_FIELDS(ctypes.Structure):
        _fields_ = [
            ("dmPosition", POINTL),
            ("dmDisplayOrientation", wintypes.DWORD),
            ("dmDisplayFixedOutput", wintypes.DWORD),
        ]

    class _DEVMODE_UNION(ctypes.Union):
        _fields_ = [("printer", _PRINTER_FIELDS), ("display", _DISPLAY_FIELDS)]

    class DEVMODEW(ctypes.Structure):
        _fields_ = [
            ("dmDeviceName", wintypes.WCHAR * CCHDEVICENAME),
            ("dmSpecVersion", wintypes.WORD),
            ("dmDriverVersion", wintypes.WORD),
            ("dmSize", wintypes.WORD),
            ("dmDriverExtra", wintypes.WORD),
            ("dmFields", wintypes.DWORD),
            ("u", _DEVMODE_UNION),
            ("dmColor", wintypes.SHORT),
            ("dmDuplex", wintypes.SHORT),
            ("dmYResolution", wintypes.SHORT),
            ("dmTTOption", wintypes.SHORT),
            ("dmCollate", wintypes.SHORT),
            ("dmFormName", wintypes.WCHAR * CCHFORMNAME),
            ("dmLogPixels", wintypes.WORD),
            ("dmBitsPerPel", wintypes.DWORD),
            ("dmPelsWidth", wintypes.DWORD),
            ("dmPelsHeight", wintypes.DWORD),
            ("dmDisplayFlags", wintypes.DWORD),
            ("dmDisplayFrequency", wintypes.DWORD),
            ("dmICMMethod", wintypes.DWORD),
            ("dmICMIntent", wintypes.DWORD),
            ("dmMediaType", wintypes.DWORD),
            ("dmDitherType", wintypes.DWORD),
            ("dmReserved1", wintypes.DWORD),
            ("dmReserved2", wintypes.DWORD),
            ("dmPanningWidth", wintypes.DWORD),
            ("dmPanningHeight", wintypes.DWORD),
        ]

    user32.EnumDisplayDevicesW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        ctypes.POINTER(DISPLAY_DEVICEW),
        wintypes.DWORD,
    ]
    user32.EnumDisplayDevicesW.restype = wintypes.BOOL

    user32.EnumDisplaySettingsW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(DEVMODEW)]
    user32.EnumDisplaySettingsW.restype = wintypes.BOOL

    user32.ChangeDisplaySettingsExW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(DEVMODEW),
        wintypes.HWND,
        wintypes.DWORD,
        wintypes.LPVOID,
    ]
    user32.ChangeDisplaySettingsExW.restype = wintypes.LONG

    def enum_displays() -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        i = 0
        while True:
            dd = DISPLAY_DEVICEW()
            dd.cb = ctypes.sizeof(DISPLAY_DEVICEW)
            ok = user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0)
            if not ok:
                break

            device_name = dd.DeviceName
            flags = int(dd.StateFlags)
            attached = bool(flags & DISPLAY_DEVICE_ATTACHED_TO_DESKTOP)

            if attached:
                dm = DEVMODEW()
                dm.dmSize = ctypes.sizeof(DEVMODEW)
                mode_ok = user32.EnumDisplaySettingsW(device_name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm))
                mode = None
                if mode_ok:
                    mode = {
                        "width": int(dm.dmPelsWidth),
                        "height": int(dm.dmPelsHeight),
                        "refresh_hz": int(dm.dmDisplayFrequency),
                        "bits_per_pixel": int(dm.dmBitsPerPel),
                        "orientation": int(dm.u.display.dmDisplayOrientation),
                    }

                out.append(
                    {
                        "id": len(out) + 1,
                        "device": device_name,
                        "name": dd.DeviceString,
                        "primary": bool(flags & DISPLAY_DEVICE_PRIMARY_DEVICE),
                        "state_flags": flags,
                        "mode": mode,
                    }
                )
            i += 1
        return out

    def _orientation_from_string(s: str) -> int:
        s = (s or "").strip().lower()
        if s in ("landscape", "default", "0"):
            return DMDO_DEFAULT
        if s in ("portrait", "90"):
            return DMDO_90
        if s in ("landscape_flipped", "180"):
            return DMDO_180
        if s in ("portrait_flipped", "270"):
            return DMDO_270
        raise ValueError("orientation must be one of: landscape, portrait, landscape_flipped, portrait_flipped")

    def change_mode(
        device: str,
        width: Optional[int] = None,
        height: Optional[int] = None,
        refresh_hz: Optional[int] = None,
        orientation: Optional[Union[str, int]] = None,
    ) -> Dict[str, Any]:
        dm = DEVMODEW()
        dm.dmSize = ctypes.sizeof(DEVMODEW)
        if not user32.EnumDisplaySettingsW(device, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
            return {"ok": False, "error": "Failed to read current display settings"}

        dm_fields = 0

        if orientation is not None:
            o = _orientation_from_string(orientation) if isinstance(orientation, str) else int(orientation)
            current_o = int(dm.u.display.dmDisplayOrientation)

            portrait_like = {DMDO_90, DMDO_270}
            if (current_o in portrait_like) != (o in portrait_like):
                dm.dmPelsWidth, dm.dmPelsHeight = dm.dmPelsHeight, dm.dmPelsWidth

            dm.u.display.dmDisplayOrientation = o
            dm_fields |= DM_DISPLAYORIENTATION

        if width is not None:
            dm.dmPelsWidth = int(width)
            dm_fields |= DM_PELSWIDTH
        if height is not None:
            dm.dmPelsHeight = int(height)
            dm_fields |= DM_PELSHEIGHT
        if refresh_hz is not None:
            dm.dmDisplayFrequency = int(refresh_hz)
            dm_fields |= DM_DISPLAYFREQUENCY

        dm.dmFields |= dm_fields

        r = user32.ChangeDisplaySettingsExW(device, ctypes.byref(dm), None, 0, None)
        if r != DISP_CHANGE_SUCCESSFUL:
            return {"ok": False, "error": f"ChangeDisplaySettingsEx failed (code={int(r)})"}
        return {"ok": True}

    return {"enum_displays": enum_displays, "change_mode": change_mode}


def display_list_displays(params: Dict[str, Any]) -> Dict[str, Any]:
    """List connected displays and their current mode."""
    err = _require_windows("display.list_displays")
    if err:
        return err
    try:
        h = _ctypes_display_helpers()
        displays = h["enum_displays"]()
        return {"result": {"count": len(displays), "displays": displays}}
    except Exception as e:
        return {"result": {"count": 0, "displays": [], "error": str(e)}}


def _pick_display_device(params: Dict[str, Any], displays: List[Dict[str, Any]]) -> Optional[str]:
    disp = params.get("display")
    if disp is None:
        for d in displays:
            if d.get("primary"):
                return d.get("device")
        return displays[0]["device"] if displays else None

    if isinstance(disp, int) or (isinstance(disp, str) and disp.isdigit()):
        idx = int(disp)
        for d in displays:
            if int(d.get("id", 0)) == idx:
                return d.get("device")
        return None

    if isinstance(disp, str):
        s = disp.strip()
        for d in displays:
            if d.get("device", "").lower() == s.lower():
                return d.get("device")
        return None

    return None


def display_set_resolution(params: Dict[str, Any]) -> Dict[str, Any]:
    """Set display resolution (defaults to primary)."""
    err = _require_windows("display.set_resolution")
    if err:
        return err

    width = params.get("width")
    height = params.get("height")
    if width is None or height is None:
        return {"error": "Missing params 'width' and/or 'height'."}
    try:
        width_i = int(width)
        height_i = int(height)
    except Exception:
        return {"error": "Params 'width' and 'height' must be integers."}

    h = _ctypes_display_helpers()
    displays = h["enum_displays"]()
    device = _pick_display_device(params, displays)
    if not device:
        return {"error": "Could not find requested display. Use display.list_displays."}

    before = next((d for d in displays if d.get("device") == device), None)
    r = h["change_mode"](device=device, width=width_i, height=height_i)
    time.sleep(0.4)
    after_displays = h["enum_displays"]()
    after = next((d for d in after_displays if d.get("device") == device), None)

    return {
        "result": {
            "ok": bool(r.get("ok")),
            "device": device,
            "requested": {"width": width_i, "height": height_i},
            "before": before,
            "after": after,
            "error": r.get("error"),
            "note": "Some modes may be rejected by your GPU/driver. Use display.list_displays to confirm the active mode.",
        }
    }


def display_set_refresh_rate(params: Dict[str, Any]) -> Dict[str, Any]:
    """Set refresh rate (Hz) (defaults to primary)."""
    err = _require_windows("display.set_refresh_rate")
    if err:
        return err

    hz = params.get("hz")
    if hz is None:
        return {"error": "Missing param 'hz' (e.g., 60, 120)."}
    try:
        hz_i = int(hz)
    except Exception:
        return {"error": "Param 'hz' must be an integer."}

    h = _ctypes_display_helpers()
    displays = h["enum_displays"]()
    device = _pick_display_device(params, displays)
    if not device:
        return {"error": "Could not find requested display. Use display.list_displays."}

    before = next((d for d in displays if d.get("device") == device), None)
    r = h["change_mode"](device=device, refresh_hz=hz_i)
    time.sleep(0.4)
    after_displays = h["enum_displays"]()
    after = next((d for d in after_displays if d.get("device") == device), None)

    return {"result": {"ok": bool(r.get("ok")), "device": device, "requested": {"hz": hz_i}, "before": before, "after": after, "error": r.get("error")}}


def display_set_orientation(params: Dict[str, Any]) -> Dict[str, Any]:
    """Set orientation: landscape/portrait/landscape_flipped/portrait_flipped."""
    err = _require_windows("display.set_orientation")
    if err:
        return err

    orientation = params.get("orientation")
    if not orientation:
        return {"error": "Missing param 'orientation' (landscape/portrait/landscape_flipped/portrait_flipped)."}

    h = _ctypes_display_helpers()
    displays = h["enum_displays"]()
    device = _pick_display_device(params, displays)
    if not device:
        return {"error": "Could not find requested display. Use display.list_displays."}

    before = next((d for d in displays if d.get("device") == device), None)
    r = h["change_mode"](device=device, orientation=str(orientation))
    time.sleep(0.4)
    after_displays = h["enum_displays"]()
    after = next((d for d in after_displays if d.get("device") == device), None)

    return {"result": {"ok": bool(r.get("ok")), "device": device, "requested": {"orientation": str(orientation)}, "before": before, "after": after, "error": r.get("error")}}


def display_set_multiple_displays(params: Dict[str, Any]) -> Dict[str, Any]:
    """Switch multi-display mode using DisplaySwitch.exe."""
    err = _require_windows("display.set_multiple_displays")
    if err:
        return err

    mode = (params.get("mode") or "").strip().lower()
    mapping = {
        "extend": "/extend",
        "duplicate": "/clone",
        "second_screen_only": "/external",
        "pc_screen_only": "/internal",
    }
    if mode not in mapping:
        return {"error": "Invalid 'mode'. Use one of: extend, duplicate, second_screen_only, pc_screen_only."}

    try:
        p = subprocess.run(["DisplaySwitch.exe", mapping[mode]], capture_output=True, text=True)
        return {
            "result": {
                "ok": p.returncode == 0,
                "mode": mode,
                "exit_code": p.returncode,
                "stdout": (p.stdout or "").strip(),
                "stderr": (p.stderr or "").strip(),
                "note": "Windows may flicker briefly while applying the new display mode.",
            }
        }
    except Exception as e:
        return {"result": {"ok": False, "mode": mode, "error": str(e)}}


def display_set_scale(params: Dict[str, Any]) -> Dict[str, Any]:
    """Set system scaling percentage for current user (usually requires sign-out/in)."""
    err = _require_windows("display.set_scale")
    if err:
        return err

    percent = params.get("percent")
    if percent is None:
        return {"error": "Missing param 'percent' (e.g., 100, 125, 150, 175, 200)."}
    try:
        p = int(str(percent).replace("%", "").strip())
    except Exception:
        return {"error": "Param 'percent' must be an integer (e.g., 125)."}

    allowed = {100: 96, 125: 120, 150: 144, 175: 168, 200: 192, 225: 216, 250: 240, 300: 288}
    if p not in allowed:
        return {"error": "Unsupported scale percent. Use one of: 100, 125, 150, 175, 200, 225, 250, 300."}

    try:
        import winreg

        key_path = r"Control Panel\\Desktop"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path, 0, winreg.KEY_SET_VALUE) as k:
            winreg.SetValueEx(k, "LogPixels", 0, winreg.REG_DWORD, allowed[p])
            winreg.SetValueEx(k, "Win8DpiScaling", 0, winreg.REG_DWORD, 1)

        return {
            "result": {
                "ok": True,
                "requested_percent": p,
                "log_pixels": allowed[p],
                "note": "Scaling changes usually need sign-out/sign-in (or reboot) to fully apply.",
            }
        }
    except Exception as e:
        return {"result": {"ok": False, "requested_percent": p, "error": str(e)}}


def display_open_color_profile(params: Dict[str, Any]) -> Dict[str, Any]:
    """Open Color Management (color profiles)."""
    err = _require_windows("display.open_color_profile")
    if err:
        return err
    try:
        subprocess.Popen(["control.exe", "colorcpl"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return {"result": {"ok": True, "note": "Opened Color Management (color profiles)."}}
    except Exception as e:
        return {"result": {"ok": False, "error": str(e)}}


def display_open_hdr_settings(params: Dict[str, Any]) -> Dict[str, Any]:
    """Open HDR area (fallback)."""
    err = _require_windows("display.open_hdr_settings")
    if err:
        return err
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:display"], shell=False)
        return {"result": {"ok": True, "note": "Opened Display settings. HDR is under Brightness & color if supported."}}
    except Exception as e:
        return {"result": {"ok": False, "error": str(e)}}


def display_open_night_light(params: Dict[str, Any]) -> Dict[str, Any]:
    """Open Night light area (fallback)."""
    err = _require_windows("display.open_night_light")
    if err:
        return err
    try:
        subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:display"], shell=False)
        return {"result": {"ok": True, "note": "Opened Display settings. Night light is under Brightness & color."}}
    except Exception as e:
        return {"result": {"ok": False, "error": str(e)}}
