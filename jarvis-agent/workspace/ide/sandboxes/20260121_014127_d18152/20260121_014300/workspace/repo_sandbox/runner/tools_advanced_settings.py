# runner/tools_advanced_settings.py
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

def _coerce_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return int(v) != 0
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "on", "enabled")
    return default

def advanced_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    System -> Advanced:
      - End task in taskbar (TaskbarEndTask)
      - File Explorer toggles:
        * show file extensions (HideFileExt)
        * show hidden & system files (Hidden + ShowSuperHidden)
        * show full path in title bar (CabinetState FullPath)
        * show empty drives (HideDrivesWithNoMedia)
        * show run as different user in Start (ShowRunAsDifferentUserInStart)
    """
    if os.name != "nt":
        return {"result": {"supported": False}}

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    cab_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\CabinetState"

    end_task_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "TaskbarEndTask")

    hide_file_ext_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "HideFileExt")

    hidden_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "Hidden")
    super_hidden_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "ShowSuperHidden")

    full_path_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, cab_path, "FullPath")
    # Some builds may store variants; we read only FullPath, but we will write it too.

    hide_empty_drives_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "HideDrivesWithNoMedia")

    run_as_diff_user_raw = _read_reg_value(winreg.HKEY_CURRENT_USER, adv_path, "ShowRunAsDifferentUserInStart")

    # Interpret values
    end_task_enabled = (int(end_task_raw) == 1) if isinstance(end_task_raw, int) else False

    show_file_extensions = not ((int(hide_file_ext_raw) == 1) if isinstance(hide_file_ext_raw, int) else False)

    show_hidden_files = (int(hidden_raw) == 1) if isinstance(hidden_raw, int) else False
    show_system_files = (int(super_hidden_raw) == 1) if isinstance(super_hidden_raw, int) else False
    show_hidden_and_system = bool(show_hidden_files and show_system_files)

    show_full_path = (int(full_path_raw) == 1) if isinstance(full_path_raw, int) else False

    show_empty_drives = not ((int(hide_empty_drives_raw) == 1) if isinstance(hide_empty_drives_raw, int) else False)

    show_run_as_diff_user = (int(run_as_diff_user_raw) == 1) if isinstance(run_as_diff_user_raw, int) else False

    return {
        "result": {
            "supported": True,
            "end_task_in_taskbar": end_task_enabled,
            "show_file_extensions": show_file_extensions,
            "show_hidden_and_system_files": show_hidden_and_system,
            "show_full_path_in_title_bar": show_full_path,
            "show_empty_drives": show_empty_drives,
            "show_run_as_different_user_in_start": show_run_as_diff_user,
            "raw": {
                "TaskbarEndTask": end_task_raw,
                "HideFileExt": hide_file_ext_raw,
                "Hidden": hidden_raw,
                "ShowSuperHidden": super_hidden_raw,
                "CabinetState.FullPath": full_path_raw,
                "HideDrivesWithNoMedia": hide_empty_drives_raw,
                "ShowRunAsDifferentUserInStart": run_as_diff_user_raw,
            },
            "note": "Some changes may require restarting Explorer or signing out/in to fully apply.",
        }
    }

def advanced_set_end_task_in_taskbar(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "TaskbarEndTask", 1 if enabled else 0)

    return {"result": {"supported": True, "end_task_in_taskbar": enabled}}

def advanced_set_show_file_extensions(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    # HideFileExt: 1 = hide extensions, 0 = show
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "HideFileExt", 0 if enabled else 1)

    return {"result": {"supported": True, "show_file_extensions": enabled}}

def advanced_set_show_hidden_and_system_files(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    # Hidden: 1 = show hidden, 2 = don't show
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "Hidden", 1 if enabled else 2)
    # ShowSuperHidden: 1 = show protected OS files, 0 = hide
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "ShowSuperHidden", 1 if enabled else 0)

    return {"result": {"supported": True, "show_hidden_and_system_files": enabled}}

def advanced_set_show_full_path_in_title_bar(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    cab_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\CabinetState"
    _write_reg_dword(winreg.HKEY_CURRENT_USER, cab_path, "FullPath", 1 if enabled else 0)

    return {"result": {"supported": True, "show_full_path_in_title_bar": enabled}}

def advanced_set_show_empty_drives(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    # HideDrivesWithNoMedia: 1 = hide empty drives, 0 = show
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "HideDrivesWithNoMedia", 0 if enabled else 1)

    return {"result": {"supported": True, "show_empty_drives": enabled}}

def advanced_set_show_run_as_different_user_in_start(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"result": {"supported": False}}

    enabled = _coerce_bool(params.get("enabled"), False)

    import winreg
    adv_path = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Advanced"
    _write_reg_dword(winreg.HKEY_CURRENT_USER, adv_path, "ShowRunAsDifferentUserInStart", 1 if enabled else 0)

    return {"result": {"supported": True, "show_run_as_different_user_in_start": enabled}}
