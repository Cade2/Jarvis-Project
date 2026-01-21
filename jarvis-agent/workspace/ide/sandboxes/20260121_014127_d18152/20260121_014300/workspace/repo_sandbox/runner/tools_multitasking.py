# runner/tools_multitasking.py
from __future__ import annotations
from typing import Any, Dict, Optional
import os

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

def _snap_enabled_from_value(v: Optional[str]) -> Optional[bool]:
    if v is None:
        return None
    s = str(v).strip()
    if s == "1":
        return True
    if s == "0":
        return False
    return None

def _alt_tab_value_to_label(v: Optional[int]) -> str:
    # From ElevenForum REG files:
    # 0=20 tabs, 1=5 tabs, 2=3 tabs (default), 3=don't show tabs
    if v == 0:
        return "20_most_recent"
    if v == 1:
        return "5_most_recent"
    if v == 2:
        return "3_most_recent"
    if v == 3:
        return "dont_show"
    return "unknown"

def _label_to_alt_tab_value(label: str) -> int:
    t = (label or "").strip().lower()
    if t in ("dont_show", "don't show", "none", "off", "0"):
        return 3
    if t in ("3", "3_most_recent", "3 tabs", "3 most recent"):
        return 2
    if t in ("5", "5_most_recent", "5 tabs", "5 most recent"):
        return 1
    if t in ("20", "20_most_recent", "20 tabs", "20 most recent", "all"):
        return 0
    raise ValueError("tabs must be one of: dont_show | 3 | 5 | 20")

def multitasking_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    import winreg

    # Snap windows main toggle
    snap_path = r"Control Panel\Desktop"
    snap_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, snap_path, "WindowArrangementActive")

    # Title bar window shake (Aero Shake)
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    shake_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "DisallowShaking")

    # Alt+Tab tabs from apps
    alt_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "MultiTaskingAltTabFilter")
    alt_val = alt_raw if isinstance(alt_raw, int) else 2  # default to 3 most recent tabs

    snap_enabled = _snap_enabled_from_value(snap_raw)
    shake_disabled = (int(shake_raw) == 1) if isinstance(shake_raw, int) else False
    shake_enabled = not shake_disabled

    return {
        "result": {
            "supported": True,
            "snap_windows": snap_enabled,
            "title_bar_window_shake": shake_enabled,
            "alt_tab_tabs": _alt_tab_value_to_label(alt_val),
            "raw": {
                "WindowArrangementActive": snap_raw,
                "DisallowShaking": shake_raw,
                "MultiTaskingAltTabFilter": alt_raw,
            },
            "note": "Some changes may require restarting Explorer or signing out/in to fully apply.",
        }
    }


def multitasking_set_snap_windows(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = params.get("enabled")
    if enabled is None:
        raise ValueError("Missing 'enabled' (true/false)")

    import winreg
    snap_path = r"Control Panel\Desktop"
    _write_reg_sz(
        winreg.HKEY_CURRENT_USER,
        snap_path,
        "WindowArrangementActive",
        "1" if bool(enabled) else "0",
    )
    return {"result": {"supported": True, "snap_windows": bool(enabled)}}

def multitasking_set_title_bar_shake(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = params.get("enabled")
    if enabled is None:
        raise ValueError("Missing 'enabled' (true/false)")

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"

    # DisallowShaking=1 means disabled. 0 (or missing) means enabled.
    _write_reg_dword(
        winreg.HKEY_CURRENT_USER,
        adv_path,
        "DisallowShaking",
        0 if bool(enabled) else 1,
    )
    return {"result": {"supported": True, "title_bar_window_shake": bool(enabled)}}

def multitasking_set_alt_tab_tabs(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    tabs = params.get("tabs")
    v = _label_to_alt_tab_value(str(tabs))

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "MultiTaskingAltTabFilter", v)
    return {"result": {"supported": True, "alt_tab_tabs": _alt_tab_value_to_label(v), "value": v}}
