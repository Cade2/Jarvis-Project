from __future__ import annotations

from typing import Any, Dict, Optional, Tuple
import subprocess
import time as _time

import os

try:
    import winreg  # type: ignore
except Exception:
    winreg = None  # type: ignore


# Registry paths
_ADVANCED = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
_TZAUTO = r"SYSTEM\CurrentControlSet\Services\tzautoupdate"
_W32TIME_PARAMS = r"SYSTEM\CurrentControlSet\Services\W32Time\Parameters"


def _is_access_denied(err: Exception) -> bool:
    msg = str(err).lower()
    return ("access is denied" in msg) or ("winerror 5" in msg) or ("0x80070005" in msg)


def _run(cmd: list[str], timeout: int = 20) -> Tuple[int, str, str]:
    p = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _ps(script: str, timeout: int = 25) -> Tuple[int, str, str]:
    return _run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], timeout=timeout)


def _reg_get_dword(root_key, path: str, name: str, default: int) -> int:
    if winreg is None:
        return default
    try:
        with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ) as k:
            v, t = winreg.QueryValueEx(k, name)
            if t == winreg.REG_DWORD:
                return int(v)
    except FileNotFoundError:
        return default
    except OSError:
        return default
    return default


def _reg_set_dword(root_key, path: str, name: str, value: int) -> None:
    if winreg is None:
        raise RuntimeError("winreg unavailable")
    with winreg.CreateKeyEx(root_key, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def _reg_get_sz(root_key, path: str, name: str, default: str) -> str:
    if winreg is None:
        return default
    try:
        with winreg.OpenKey(root_key, path, 0, winreg.KEY_READ) as k:
            v, t = winreg.QueryValueEx(k, name)
            if t == winreg.REG_SZ:
                return str(v)
    except FileNotFoundError:
        return default
    except OSError:
        return default
    return default


def _reg_set_sz(root_key, path: str, name: str, value: str) -> None:
    if winreg is None:
        raise RuntimeError("winreg unavailable")
    with winreg.CreateKeyEx(root_key, path, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))


def _get_timezone_id() -> Optional[str]:
    code, out, _ = _run(["tzutil", "/g"], timeout=10)
    if code == 0 and out:
        return out
    return None


def _get_service_state(service_name: str) -> Dict[str, Any]:
    ps = (
        f"$s = Get-Service -Name '{service_name}' -ErrorAction SilentlyContinue; "
        f"if ($null -eq $s) {{ '' }} else {{ $s.Status.ToString() }}"
    )
    code, out, _ = _ps(ps, timeout=10)
    status = out if code == 0 and out else None

    ps2 = (
        f"$w = Get-CimInstance Win32_Service -Filter \"Name='{service_name}'\" -ErrorAction SilentlyContinue; "
        f"if ($null -eq $w) {{ '' }} else {{ $w.StartMode }}"
    )
    code2, out2, _ = _ps(ps2, timeout=10)
    start_mode = out2 if code2 == 0 and out2 else None

    return {"status": status, "start_mode": start_mode}


def time_get_state(_: Dict[str, Any]) -> Dict[str, Any]:
    tz_id = _get_timezone_id()

    # Auto timezone (HKLM): Start 3 = enabled, 4 = disabled
    tz_auto_start = _reg_get_dword(winreg.HKEY_LOCAL_MACHINE, _TZAUTO, "Start", default=4) if winreg else 4
    auto_timezone_enabled = (tz_auto_start == 3)

    # Auto time (HKLM): W32Time Parameters\Type "NTP" enabled, "NoSync" disabled
    w32_type = _reg_get_sz(winreg.HKEY_LOCAL_MACHINE, _W32TIME_PARAMS, "Type", default="") if winreg else ""
    auto_time_enabled = (w32_type.strip().lower() != "nosync" and w32_type.strip() != "")

    # System tray show/hide (HKCU): 1 = show, 0 = hide
    show_systray_datetime = _reg_get_dword(
        winreg.HKEY_CURRENT_USER, _ADVANCED, "ShowSystrayDateTimeValueName", default=1
    ) if winreg else 1

    # Notification Center show/hide (HKCU): 1 = show, 0 = hide
    show_clock_nc = _reg_get_dword(
        winreg.HKEY_CURRENT_USER, _ADVANCED, "ShowClockInNotificationCenter", default=0
    ) if winreg else 0

    last_sync = None
    code, out, _ = _run(["w32tm", "/query", "/status"], timeout=10)
    if code == 0 and out:
        for line in out.splitlines():
            if "Last Successful Sync Time" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    last_sync = parts[1].strip()
                break

    return {
        "supported": True,
        "timezone_id": tz_id,
        "auto_timezone_enabled": auto_timezone_enabled,
        "auto_time_enabled": auto_time_enabled,
        "time_service": _get_service_state("w32time"),
        "tzautoupdate_service": _get_service_state("tzautoupdate"),
        "show_time_date_in_system_tray": bool(show_systray_datetime == 1),
        "show_time_in_notification_center": bool(show_clock_nc == 1),
        "last_successful_time_sync": last_sync,
        "notes": [
            "Some settings require Administrator rights (HKLM registry writes).",
            "If toggles don't visually update immediately, sign out/in or restart Explorer.",
        ],
    }


def time_set_auto_timezone(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_dword(winreg.HKEY_LOCAL_MACHINE, _TZAUTO, "Start", 3 if enabled else 4)
    except Exception as e:
        if _is_access_denied(e):
            return {
                "supported": False,
                "needs_elevation": True,
                "error": f"Admin rights required to change auto timezone: {e}",
                "hint": "Run: elevate runner (or restart Jarvis as admin) then retry.",
            }
        return {"supported": False, "error": f"Failed to update tzautoupdate Start value: {e}"}

    # Start/stop service best-effort (will also require admin in most cases)
    if enabled:
        _ps("Start-Service tzautoupdate -ErrorAction SilentlyContinue | Out-Null", timeout=15)
    else:
        _ps("Stop-Service tzautoupdate -Force -ErrorAction SilentlyContinue | Out-Null", timeout=15)

    return {
        "supported": True,
        "requested_enabled": enabled,
        "tzautoupdate_start": 3 if enabled else 4,
        "note": "If this option is grayed out in Settings, Location Services may be disabled.",
    }



def time_set_timezone(params: Dict[str, Any]) -> Dict[str, Any]:
    tz_id = str(params.get("timezone_id", "")).strip().strip('"')
    if not tz_id:
        return {"supported": False, "error": "Missing required parameter: timezone_id"}

    if winreg is not None:
        tz_auto_start = _reg_get_dword(winreg.HKEY_LOCAL_MACHINE, _TZAUTO, "Start", default=4)
        if tz_auto_start == 3:
            return {
                "supported": False,
                "error": "Cannot set timezone while 'Set time zone automatically' is ON. Turn it off first.",
                "hint": "Try: set time zone automatically off",
            }

    code, out, err = _run(["tzutil", "/s", tz_id], timeout=15)
    if code != 0:
        return {
            "supported": False,
            "error": err or out or "tzutil failed",
            "timezone_id": tz_id,
            "hint": 'You can list time zones with: tzutil /l (Command Prompt).',
        }

    new_tz = _get_timezone_id()
    return {"supported": True, "timezone_id": tz_id, "current_timezone_id": new_tz}


def time_set_auto_time(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_sz(
            winreg.HKEY_LOCAL_MACHINE,
            _W32TIME_PARAMS,
            "Type",
            "NTP" if enabled else "NoSync",
        )
    except Exception as e:
        if _is_access_denied(e):
            return {
                "supported": False,
                "needs_elevation": True,
                "error": f"Admin rights required to change auto time: {e}",
                "hint": "Run: elevate runner (or restart Jarvis as admin) then retry.",
            }
        return {"supported": False, "error": f"Failed to update W32Time Type: {e}"}

    # Start/stop service best-effort (also may require admin, but registry write is the main gate)
    if enabled:
        _ps("Set-Service w32time -StartupType Automatic -ErrorAction SilentlyContinue | Out-Null", timeout=15)
        _ps("Start-Service w32time -ErrorAction SilentlyContinue | Out-Null", timeout=15)
    else:
        _ps("Stop-Service w32time -Force -ErrorAction SilentlyContinue | Out-Null", timeout=15)

    return {
        "supported": True,
        "requested_enabled": enabled,
        "w32time_type": "NTP" if enabled else "NoSync",
    }



def time_sync_now(_: Dict[str, Any]) -> Dict[str, Any]:
    _ps("Start-Service w32time -ErrorAction SilentlyContinue | Out-Null", timeout=10)
    _time.sleep(0.3)

    code, out, err = _run(["w32tm", "/resync", "/force"], timeout=20)
    if code != 0:
        msg = (err or out or "w32tm resync failed")
        if "0x80070005" in msg.lower() or "access is denied" in msg.lower():
            return {
                "supported": False,
                "needs_elevation": True,
                "error": msg,
                "hint": "Admin rights required to resync time. Elevate runner and retry.",
            }
        return {"supported": False, "error": msg}


    code2, out2, _ = _run(["w32tm", "/query", "/status"], timeout=10)
    last_sync = None
    if code2 == 0 and out2:
        for line in out2.splitlines():
            if "Last Successful Sync Time" in line:
                parts = line.split(":", 1)
                if len(parts) == 2:
                    last_sync = parts[1].strip()
                break

    return {"supported": True, "output": out, "last_successful_time_sync": last_sync}


def time_set_show_systray_datetime(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    apply_now = bool(params.get("apply_now", False))

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ADVANCED, "ShowSystrayDateTimeValueName", 1 if enabled else 0)
    except Exception as e:
        return {"supported": False, "error": f"Failed to set ShowSystrayDateTimeValueName: {e}"}

    if apply_now:
        _ps("Stop-Process -Name explorer -Force -ErrorAction SilentlyContinue; Start-Process explorer.exe", timeout=20)
        note = "Explorer was restarted to apply the change."
    else:
        note = "May require sign out/in or restarting Explorer to apply."

    return {"supported": True, "requested_enabled": enabled, "apply_now": apply_now, "note": note}


def time_set_show_clock_notification_center(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ADVANCED, "ShowClockInNotificationCenter", 1 if enabled else 0)
    except Exception as e:
        return {"supported": False, "error": f"Failed to set ShowClockInNotificationCenter: {e}"}

    return {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply."}
