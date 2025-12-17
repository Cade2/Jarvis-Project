from __future__ import annotations
from typing import Any, Callable, Dict
import platform
from .tools_settings import settings_open


from .tools_system import system_get_info, system_get_storage
from .tools_apps import apps_list_installed, apps_open, apps_close
from .tools_network import network_get_state, network_toggle_wifi, network_toggle_airplane_mode
from .tools_display import display_get_state, display_set_brightness
from .tools_runner import runner_is_elevated
from .tools_bluetooth import bluetooth_get_state, bluetooth_toggle, bluetooth_list_paired






TOOL_FUNCS: Dict[str, Callable[[Dict[str, Any]], Dict[str, Any]]] = {
    "system.get_info": system_get_info,
    "system.get_storage": system_get_storage,
    "apps.list_installed": apps_list_installed,
    "apps.open": apps_open,
    "apps.close": apps_close,
    "settings.open": settings_open,  # ✅ add

    "network.get_state": network_get_state,
    "network.toggle_wifi": network_toggle_wifi,
    "network.toggle_airplane_mode": network_toggle_airplane_mode,

    "display.get_state": display_get_state,
    "display.set_brightness": display_set_brightness,

    "runner.is_elevated": runner_is_elevated,

    "bluetooth.get_state": bluetooth_get_state,
    "bluetooth.toggle": bluetooth_toggle,
    "bluetooth.list_paired": bluetooth_list_paired,

    

}


def capabilities() -> Dict[str, Any]:
    return {
        "os": platform.platform(),
        "tools": sorted(TOOL_FUNCS.keys()),
        "uia_supported": True,
    }

def run_tool(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return TOOL_FUNCS[name](params)
