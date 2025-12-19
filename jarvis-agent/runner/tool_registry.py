from __future__ import annotations
from typing import Any, Callable, Dict
import platform
from .tools_settings import settings_open


from .tools_system import system_get_info, system_get_storage
from .tools_apps import apps_list_installed, apps_open, apps_close
from .tools_network import network_get_state, network_toggle_wifi, network_toggle_airplane_mode
from .tools_display import (
    display_get_state,
    display_set_brightness,
    display_list_displays,
    display_set_resolution,
    display_set_refresh_rate,
    display_set_orientation,
    display_set_multiple_displays,
    display_set_scale,
    display_open_color_profile,
    display_open_hdr_settings,
    display_open_night_light,
)

from .tools_runner import runner_is_elevated
from .tools_bluetooth import bluetooth_get_state, bluetooth_toggle, bluetooth_list_paired, bluetooth_connect_paired
from .tools_uia import uia_get_status
from .tools_audio import audio_get_state, audio_set_volume, audio_set_mute
from .tools_power import power_get_state, power_list_schemes, power_set_scheme
from .tools_power import power_get_mode, power_set_mode

from runner.tools_power_timeouts import (
    power_get_timeouts,
    power_set_sleep_timeout,
    power_set_screen_timeout,
    power_set_hibernate_timeout,
    power_hibernate_status,
    power_hibernate_on,
    power_hibernate_off,
    power_energy_saver_status,
    power_energy_saver_on,
    power_energy_saver_off,
    power_energy_saver_threshold,
    power_battery_report,
    power_open_battery_usage,
)

from runner.tools_power_usage import power_srum_report
from .tools_storage import storage_get_categories, storage_cleanup_recommendations






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

    # Display (MK2 additions)
    "display.list_displays": display_list_displays,
    "display.set_resolution": display_set_resolution,
    "display.set_refresh_rate": display_set_refresh_rate,
    "display.set_orientation": display_set_orientation,
    "display.set_multiple_displays": display_set_multiple_displays,
    "display.set_scale": display_set_scale,
    "display.open_color_profile": display_open_color_profile,
    "display.open_hdr_settings": display_open_hdr_settings,
    "display.open_night_light": display_open_night_light,


    "runner.is_elevated": runner_is_elevated,

    "bluetooth.get_state": bluetooth_get_state,
    "bluetooth.toggle": bluetooth_toggle,
    "bluetooth.list_paired": bluetooth_list_paired,
    "bluetooth.connect_paired": bluetooth_connect_paired,

    "uia.get_status": uia_get_status,

    "audio.get_state": audio_get_state,
    "audio.set_volume": audio_set_volume,
    "audio.set_mute": audio_set_mute,

    "power.get_state": power_get_state,
    "power.list_schemes": power_list_schemes,
    "power.set_scheme": power_set_scheme,

    "power.get_mode": power_get_mode,
    "power.set_mode": power_set_mode,

    # ----- Power timeouts / energy saver / battery usage (MK2) -----
    "power.get_timeouts": power_get_timeouts,
    "power.set_sleep_timeout": power_set_sleep_timeout,
    "power.set_screen_timeout": power_set_screen_timeout,
    "power.set_hibernate_timeout": power_set_hibernate_timeout,

    "power.hibernate_status": power_hibernate_status,
    "power.hibernate_on": power_hibernate_on,
    "power.hibernate_off": power_hibernate_off,

    "power.energy_saver_status": power_energy_saver_status,
    "power.energy_saver_on": power_energy_saver_on,
    "power.energy_saver_off": power_energy_saver_off,
    "power.energy_saver_threshold": power_energy_saver_threshold,

    "power.battery_report": power_battery_report,
    "power.open_battery_usage": power_open_battery_usage,

    "power.srum_report": power_srum_report,
    
    "storage.get_categories": storage_get_categories,
    "storage.cleanup_recommendations": storage_cleanup_recommendations,

    



}


def capabilities() -> Dict[str, Any]:
    return {
        "os": platform.platform(),
        "tools": sorted(TOOL_FUNCS.keys()),
        "uia_supported": True,
    }

def run_tool(name: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return TOOL_FUNCS[name](params)

