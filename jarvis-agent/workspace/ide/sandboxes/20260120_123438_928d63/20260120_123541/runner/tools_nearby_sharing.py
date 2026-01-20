# runner/tools_nearby_sharing.py
from __future__ import annotations
from typing import Any, Dict, Optional
import os

def _is_admin() -> bool:
    if os.name != "nt":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False

def _read_reg_value(root, path: str, name: str) -> Optional[Any]:
    try:
        import winreg
        k = winreg.OpenKey(root, path)
        v, _t = winreg.QueryValueEx(k, name)
        return v
    except Exception:
        return None

def _write_reg_dword(root, path: str, name: str, value: int) -> None:
    import winreg
    k = winreg.CreateKey(root, path)
    winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))

def _write_reg_sz(root, path: str, name: str, value: str) -> None:
    import winreg
    k = winreg.CreateKey(root, path)
    winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))

def _mode_to_int(mode: str) -> int:
    m = (mode or "").strip().lower()
    if m in ("off", "disabled", "0"):
        return 0
    if m in ("my_devices_only", "my_devices", "my devices", "my devices only", "1"):
        return 1
    if m in ("everyone_nearby", "everyone", "everyone nearby", "2"):
        return 2
    raise ValueError("mode must be one of: off | my_devices_only | everyone_nearby")


def _int_to_mode(n: Optional[int]) -> str:
    if n == 0:
        return "off"
    if n == 1:
        return "my_devices_only"
    if n == 2:
        return "everyone_nearby"
    return "unknown"

def nearby_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Read Nearby sharing mode + friendly name (best effort).
    Mode is stored in HKCU CDP values (0/1/2). :contentReference[oaicite:0]{index=0}
    Friendly name can be stored in HKLM Tcpip Parameters ShareFriendlyDeviceName. :contentReference[oaicite:1]{index=1}
    """
    if os.name != "nt":
        return {"result": {"supported": False}}

    import winreg

    cdp = r"Software\Microsoft\Windows\CurrentVersion\CDP"
    cdp_settings = r"Software\Microsoft\Windows\CurrentVersion\CDP\SettingsPage"

    v1 = _read_reg_value(winreg.HKEY_CURRENT_USER, cdp, "NearShareChannelUserAuthzPolicy")
    v2 = _read_reg_value(winreg.HKEY_CURRENT_USER, cdp, "CdpSessionUserAuthzPolicy")
    v3 = _read_reg_value(winreg.HKEY_CURRENT_USER, cdp_settings, "NearShareChannelUserAuthzPolicy")

    # Prefer the more specific setting if present, else fall back
    active_raw = v3 if v3 is not None else (v1 if v1 is not None else v2)

    friendly = _read_reg_value(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
        "ShareFriendlyDeviceName",
    )

    return {
        "result": {
            "supported": True,
            "mode": _int_to_mode(active_raw if isinstance(active_raw, int) else None),
            "raw": {
                "NearShareChannelUserAuthzPolicy": v1,
                "CdpSessionUserAuthzPolicy": v2,
                "SettingsPage.NearShareChannelUserAuthzPolicy": v3,
            },
            "friendly_name": friendly,
            "admin_required_for_rename": True,
        }
    }

def nearby_set_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set Nearby sharing mode:
      0=Off, 1=My devices only, 2=Everyone nearby :contentReference[oaicite:2]{index=2}
    """
    if os.name != "nt":
        return {"result": {"supported": False}}

    mode = params.get("mode")
    v = _mode_to_int(str(mode))

    import winreg
    cdp = r"Software\Microsoft\Windows\CurrentVersion\CDP"
    cdp_settings = r"Software\Microsoft\Windows\CurrentVersion\CDP\SettingsPage"

    # Write to the common known values (best effort)
    _write_reg_dword(winreg.HKEY_CURRENT_USER, cdp, "NearShareChannelUserAuthzPolicy", v)
    _write_reg_dword(winreg.HKEY_CURRENT_USER, cdp, "CdpSessionUserAuthzPolicy", v)
    _write_reg_dword(winreg.HKEY_CURRENT_USER, cdp_settings, "NearShareChannelUserAuthzPolicy", v)

    return {"result": {"supported": True, "mode": _int_to_mode(v), "value": v}}

def nearby_set_friendly_name(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set friendly discoverable name for Nearby sharing (Windows 11 build-dependent). :contentReference[oaicite:3]{index=3}
    Writes: HKLM\\SYSTEM\\CurrentControlSet\\Services\\Tcpip\\Parameters\\ShareFriendlyDeviceName
    Requires admin.
    """
    if os.name != "nt":
        return {"result": {"supported": False}}

    name = (params.get("name") or "").strip()
    if not name:
        raise ValueError("Missing 'name'")

    if not _is_admin():
        return {
            "result": {
                "supported": True,
                "changed": False,
                "error": "Admin required. Run: elevate runner",
            }
        }

    import winreg
    _write_reg_sz(
        winreg.HKEY_LOCAL_MACHINE,
        r"SYSTEM\CurrentControlSet\Services\Tcpip\Parameters",
        "ShareFriendlyDeviceName",
        name,
    )
    return {"result": {"supported": True, "changed": True, "friendly_name": name}}
