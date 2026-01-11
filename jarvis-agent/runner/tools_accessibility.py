from __future__ import annotations

from typing import Any, Dict, Optional
import os
import subprocess

try:
    import winreg  # type: ignore
except Exception:
    winreg = None


# ----------------------------
# Registry paths (HKCU)
# ----------------------------
_TEXT_SCALE = r"Software\Microsoft\Accessibility"
_ACCESSIBILITY = r"Control Panel\Accessibility"
_PERSONALIZE = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"
_WINDOWMETRICS = r"Control Panel\Desktop\WindowMetrics"


def _ps(cmd: str, timeout: int = 10) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _apply_user_settings_best_effort() -> None:
    # Best-effort refresh. Some changes may still require sign out/in.
    try:
        _ps("RUNDLL32.EXE user32.dll,UpdatePerUserSystemParameters 1, True | Out-Null", timeout=10)
    except Exception:
        pass


def _reg_get_dword(root, path: str, name: str) -> Optional[int]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ == winreg.REG_DWORD:
                return int(val)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def _reg_set_dword(root, path: str, name: str, value: int) -> None:
    if winreg is None:
        raise RuntimeError("winreg unavailable")
    with winreg.CreateKey(root, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_DWORD, int(value))


def _reg_get_sz(root, path: str, name: str) -> Optional[str]:
    if winreg is None:
        return None
    try:
        with winreg.OpenKey(root, path, 0, winreg.KEY_READ) as k:
            val, typ = winreg.QueryValueEx(k, name)
            if typ in (winreg.REG_SZ, winreg.REG_EXPAND_SZ):
                return str(val)
    except FileNotFoundError:
        return None
    except OSError:
        return None
    return None


def _reg_set_sz(root, path: str, name: str, value: str) -> None:
    if winreg is None:
        raise RuntimeError("winreg unavailable")
    with winreg.CreateKey(root, path) as k:
        winreg.SetValueEx(k, name, 0, winreg.REG_SZ, str(value))


# ----------------------------
# Vision: status (text size + visual effects)
# ----------------------------
def accessibility_get_vision_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    # Text size (percent)
    text_scale = _reg_get_dword(winreg.HKEY_CURRENT_USER, _TEXT_SCALE, "TextScaleFactor")

    # Always show scrollbars:
    # DynamicScrollbars = 1 (auto hide), 0 (always show)
    dyn = _reg_get_dword(winreg.HKEY_CURRENT_USER, _ACCESSIBILITY, "DynamicScrollbars")
    always_show_scrollbars = True if dyn == 0 else False if dyn == 1 else None

    # Transparency effects: EnableTransparency = 1/0
    transparency = _reg_get_dword(winreg.HKEY_CURRENT_USER, _PERSONALIZE, "EnableTransparency")
    transparency_enabled = True if transparency == 1 else False if transparency == 0 else None

    # Animation effects (best-effort): MinAnimate = "1"/"0"
    min_animate = _reg_get_sz(winreg.HKEY_CURRENT_USER, _WINDOWMETRICS, "MinAnimate")
    animation_enabled = None
    if min_animate is not None:
        animation_enabled = True if min_animate.strip() == "1" else False if min_animate.strip() == "0" else None

    # Dismiss notifications after this time: MessageDuration in seconds
    msg_dur = _reg_get_dword(winreg.HKEY_CURRENT_USER, _ACCESSIBILITY, "MessageDuration")

    return {
        "result": {
            "supported": True,
            "text_size_percent": text_scale,
            "always_show_scrollbars": always_show_scrollbars,
            "transparency_effects": transparency_enabled,
            "animation_effects": animation_enabled,
            "dismiss_notifications_after_seconds": msg_dur,
            "notes": [
                "Some changes may require reopening Settings or signing out/in to visually update everywhere.",
            ],
        }
    }


# ----------------------------
# Text size
# ----------------------------
def accessibility_set_text_size(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    percent = int(params.get("percent", 100))
    # Windows UI slider commonly supports 100–225
    if percent < 100 or percent > 225:
        return {"result": {"supported": False, "error": "percent must be between 100 and 225."}}

    _reg_set_dword(winreg.HKEY_CURRENT_USER, _TEXT_SCALE, "TextScaleFactor", percent)
    _apply_user_settings_best_effort()

    return {"result": {"supported": True, "requested_percent": percent, "note": "May require sign out/in to fully apply."}}


# ----------------------------
# Visual effects toggles
# ----------------------------
def accessibility_set_always_show_scrollbars(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    enabled = bool(params.get("enabled", True))
    # 0 = always show, 1 = auto hide
    _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESSIBILITY, "DynamicScrollbars", 0 if enabled else 1)
    _apply_user_settings_best_effort()

    return {"result": {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply everywhere."}}


def accessibility_set_transparency_effects(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    enabled = bool(params.get("enabled", True))
    _reg_set_dword(winreg.HKEY_CURRENT_USER, _PERSONALIZE, "EnableTransparency", 1 if enabled else 0)
    _apply_user_settings_best_effort()

    return {"result": {"supported": True, "requested_enabled": enabled, "note": "May require reopening Settings to see the change."}}


def accessibility_set_animation_effects(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    enabled = bool(params.get("enabled", True))
    _reg_set_sz(winreg.HKEY_CURRENT_USER, _WINDOWMETRICS, "MinAnimate", "1" if enabled else "0")
    _apply_user_settings_best_effort()

    return {"result": {"supported": True, "requested_enabled": enabled, "note": "May require sign out/in to apply everywhere."}}


def accessibility_set_dismiss_notifications_after(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt" or winreg is None:
        return {"result": {"supported": False, "error": "Windows only (winreg unavailable)."}}

    seconds = int(params.get("seconds", 5))
    allowed = {5, 7, 15, 30, 60, 300}
    if seconds not in allowed:
        return {"result": {"supported": False, "error": f"seconds must be one of {sorted(allowed)}."}}

    _reg_set_dword(winreg.HKEY_CURRENT_USER, _ACCESSIBILITY, "MessageDuration", seconds)
    _apply_user_settings_best_effort()

    return {"result": {"supported": True, "requested_seconds": seconds, "note": "May require reopening Settings to see the change."}}
