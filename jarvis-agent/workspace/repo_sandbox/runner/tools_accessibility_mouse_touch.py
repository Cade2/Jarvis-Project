from __future__ import annotations

from typing import Any, Dict, Optional
import ctypes

try:
    import winreg
except Exception:
    winreg = None  # type: ignore


# -------------------------
# Registry helpers
# -------------------------

def _is_access_denied(e: Exception) -> bool:
    return "Access is denied" in str(e) or getattr(e, "winerror", None) == 5

def _reg_get_dword(root, path: str, name: str) -> Optional[int]:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ == winreg.REG_DWORD:
                return int(val)
    except Exception:
        return None
    return None

def _reg_set_dword(root, path: str, name: str, value: int) -> None:
    with winreg.CreateKey(root, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))

def _reg_get_sz(root, path: str, name: str) -> Optional[str]:
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                return str(val)
    except Exception:
        return None
    return None

def _reg_set_sz(root, path: str, name: str, value: str) -> None:
    with winreg.CreateKey(root, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))


# -------------------------
# Broadcast / apply helpers
# -------------------------

def _update_per_user_system_parameters() -> None:
    # Helps some HKCU settings apply without logoff
    try:
        ctypes.windll.user32.SendMessageTimeoutW(
            0xFFFF,  # HWND_BROADCAST
            0x001A,  # WM_SETTINGCHANGE
            0,
            "Windows",
            0x0002,  # SMTO_ABORTIFHUNG
            2000,
            None,
        )
    except Exception:
        pass

def _spi_set_mouse_trails(length: int) -> None:
    # SPI_SETMOUSETRAILS = 0x005D
    try:
        ctypes.windll.user32.SystemParametersInfoW(0x005D, int(length), None, 0)
    except Exception:
        pass


# -------------------------
# Paths / constants
# -------------------------

_ACCESS = r"Software\Microsoft\Accessibility"
_MOUSE = r"Control Panel\Mouse"
_CURSORS = r"Control Panel\Cursors"

# CursorType (best-effort mapping)
# 0=white, 1=black, 2=inverted, 3=custom
_STYLE_TO_TYPE = {"white": 0, "black": 1, "inverted": 2, "custom": 3}
_TYPE_TO_STYLE = {v: k for k, v in _STYLE_TO_TYPE.items()}

# Approx ARGB colors (best-effort)
COLOR_MAP = {
    "purple":    0xFFA200FF,
    "lime":      0xFFB8FF00,
    "yellow":    0xFFFFFF00,
    "gold":      0xFFFFB900,
    "pink":      0xFFFF2D95,
    "turquoise": 0xFF00C2FF,
    "green":     0xFF00CC6A,
}


# -------------------------
# GET state
# -------------------------

def accessibility_get_mouse_touch_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    cursor_type = _reg_get_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorType")
    cursor_size = _reg_get_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorSize")
    cursor_color = _reg_get_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorColor")

    sonar = _reg_get_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseSonar")
    trails = _reg_get_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseTrails")
    shadow = _reg_get_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseShadow")

    # Touch indicator (best-effort; commonly used keys)
    contact_vis = _reg_get_dword(winreg.HKEY_CURRENT_USER, _CURSORS, "ContactVisualization")

    try:
        trails_int = int(trails) if trails and trails.isdigit() else 0
    except Exception:
        trails_int = 0

    style = _TYPE_TO_STYLE.get(cursor_type, None)
    touch_enabled = (contact_vis is not None and contact_vis > 0)
    touch_enhanced = (contact_vis == 2)

    return {
        "supported": True,
        "mouse_pointer_style": style,
        "mouse_pointer_size": cursor_size,
        "mouse_pointer_color_argb": cursor_color,
        "mouse_indicator": True if sonar == "1" else False if sonar == "0" else None,
        "mouse_pointer_trails": True if trails_int > 0 else False,
        "mouse_pointer_trails_length": trails_int if trails_int > 0 else 0,
        "mouse_pointer_shadow": True if shadow == "1" else False if shadow == "0" else None,
        "touch_indicator": touch_enabled,
        "touch_indicator_darker_larger": touch_enhanced,
        "notes": [
            "Some changes may need sign out/in to reflect everywhere.",
            "Pointer style/color/size keys are best-effort across Windows builds.",
        ],
    }


# -------------------------
# SET: pointer style / color / size
# -------------------------

def accessibility_set_mouse_pointer_style(params: Dict[str, Any]) -> Dict[str, Any]:
    style = str(params.get("style", "")).strip().lower()
    color_name = str(params.get("color", "")).strip().lower()

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}
    if style not in _STYLE_TO_TYPE:
        return {"supported": False, "error": "Invalid style. Use: white, black, inverted, custom"}

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorType", _STYLE_TO_TYPE[style])

        # If custom, optionally set color
        if style == "custom":
            if color_name:
                if color_name not in COLOR_MAP:
                    return {"supported": False, "error": f"Invalid custom color. Use: {', '.join(COLOR_MAP.keys())}"}
                _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorColor", COLOR_MAP[color_name])

        _update_per_user_system_parameters()
        return {
            "supported": True,
            "requested_style": style,
            "requested_color": color_name or None,
            "note": "If Settings UI doesn't update immediately, reopen Settings.",
        }
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set pointer style: {e}"}

def accessibility_set_mouse_pointer_size(params: Dict[str, Any]) -> Dict[str, Any]:
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        size = int(params.get("size"))
    except Exception:
        return {"supported": False, "error": "size must be an integer"}

    # Best-effort clamp (Windows typically uses a small range)
    size = max(1, min(15, size))

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorSize", size)
        _update_per_user_system_parameters()
        return {"supported": True, "requested_size": size, "note": "May require sign out/in to fully apply."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set pointer size: {e}"}

def accessibility_set_mouse_pointer_color(params: Dict[str, Any]) -> Dict[str, Any]:
    color_name = str(params.get("color", "")).strip().lower()
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}
    if color_name not in COLOR_MAP:
        return {"supported": False, "error": f"Invalid color. Use: {', '.join(COLOR_MAP.keys())}"}

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorType", _STYLE_TO_TYPE["custom"])
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESS, "CursorColor", COLOR_MAP[color_name])
        _update_per_user_system_parameters()
        return {"supported": True, "requested_color": color_name, "note": "If Settings UI doesn't update immediately, reopen Settings."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set pointer color: {e}"}


# -------------------------
# SET: mouse indicator / trails / shadow
# -------------------------

def accessibility_set_mouse_indicator(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseSonar", "1" if enabled else "0")
        _update_per_user_system_parameters()
        return {"supported": True, "recognize_ctrl_key_circle": enabled, "note": "May require sign out/in to apply everywhere."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set mouse indicator: {e}"}

def accessibility_set_mouse_pointer_trails(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    length = params.get("length", 10)

    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        length_i = int(length)
    except Exception:
        length_i = 10

    length_i = max(1, min(20, length_i))

    try:
        if enabled:
            _reg_set_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseTrails", str(length_i))
            _spi_set_mouse_trails(length_i)
        else:
            _reg_set_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseTrails", "0")
            _spi_set_mouse_trails(0)

        _update_per_user_system_parameters()
        return {
            "supported": True,
            "requested_enabled": enabled,
            "requested_length": length_i if enabled else 0,
            "note": "May require sign out/in to apply everywhere.",
        }
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set pointer trails: {e}"}

def accessibility_set_mouse_pointer_trails_length(params: Dict[str, Any]) -> Dict[str, Any]:
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        length_i = int(params.get("length"))
    except Exception:
        return {"supported": False, "error": "length must be an integer"}

    length_i = max(1, min(20, length_i))

    try:
        _reg_set_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseTrails", str(length_i))
        _spi_set_mouse_trails(length_i)
        _update_per_user_system_parameters()
        return {"supported": True, "requested_length": length_i, "note": "May require sign out/in to apply everywhere."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set trails length: {e}"}

def accessibility_set_mouse_pointer_shadow(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_sz(winreg.HKEY_CURRENT_USER, _MOUSE, "MouseShadow", "1" if enabled else "0")
        _update_per_user_system_parameters()
        return {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply everywhere."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set pointer shadow: {e}"}


# -------------------------
# SET: touch indicator + enhanced option
# -------------------------

def accessibility_set_touch_indicator(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _CURSORS, "ContactVisualization", 1 if enabled else 0)
        _update_per_user_system_parameters()
        return {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply everywhere."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set touch indicator: {e}"}

def accessibility_set_touch_indicator_enhanced(params: Dict[str, Any]) -> Dict[str, Any]:
    enabled = bool(params.get("enabled", True))
    if winreg is None:
        return {"supported": False, "error": "winreg unavailable on this platform."}

    current = _reg_get_dword(winreg.HKEY_CURRENT_USER, _CURSORS, "ContactVisualization") or 0
    if current == 0 and enabled:
        return {
            "supported": False,
            "error": "Touch indicator is OFF. Turn it on first.",
            "hint": "Try: touch indicator on",
        }

    try:
        _reg_set_dword(winreg.HKEY_CURRENT_USER, _CURSORS, "ContactVisualization", 2 if enabled else 1)
        _update_per_user_system_parameters()
        return {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply everywhere."}
    except Exception as e:
        if _is_access_denied(e):
            return {"supported": False, "needs_elevation": True, "error": f"Access denied: {e}"}
        return {"supported": False, "error": f"Failed to set enhanced touch indicator: {e}"}
