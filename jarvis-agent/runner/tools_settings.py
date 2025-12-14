from __future__ import annotations
from typing import Any, Dict
import os
import subprocess
import time

import psutil

# Simple topic -> ms-settings URI map (we can grow this over time)
SETTINGS_MAP = {
    # System / Display / Sound / Power
    "system": "ms-settings:system",
    "display": "ms-settings:display",
    "night light": "ms-settings:nightlight",
    "sound": "ms-settings:sound",
    "notifications": "ms-settings:notifications",
    "focus": "ms-settings:quietmomentshome",
    "power": "ms-settings:powersleep",
    "battery": "ms-settings:batterysaver",

    # Network & Internet
    "network": "ms-settings:network",
    "wifi": "ms-settings:network-wifi",
    "airplane mode": "ms-settings:network-airplanemode",
    "vpn": "ms-settings:network-vpn",
    "proxy": "ms-settings:network-proxy",
    "ethernet": "ms-settings:network-ethernet",

    # Bluetooth & devices
    "bluetooth": "ms-settings:bluetooth",
    "devices": "ms-settings:devices",

    # Personalization
    "personalization": "ms-settings:personalization",
    "themes": "ms-settings:themes",
    "colors": "ms-settings:colors",
    "lock screen": "ms-settings:lockscreen",

    # Apps
    "apps": "ms-settings:appsfeatures",
    "default apps": "ms-settings:defaultapps",
    "startup apps": "ms-settings:startupapps",

    # Accounts (we will OPEN only; no account actions/tools)
    "accounts": "ms-settings:yourinfo",

    # Time & language
    "time": "ms-settings:dateandtime",
    "date and time": "ms-settings:dateandtime",
    "language": "ms-settings:regionlanguage",
    "region": "ms-settings:regionlanguage",

    # Privacy & security
    "privacy": "ms-settings:privacy",
    "windows security": "ms-settings:windowsdefender",
    "updates": "ms-settings:windowsupdate",
    "windows update": "ms-settings:windowsupdate",
}


def _is_settings_running() -> bool:
    for p in psutil.process_iter(attrs=["name"]):
        try:
            if (p.info.get("name") or "").lower() == "systemsettings.exe":
                return True
        except Exception:
            continue
    return False


def settings_open(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Open Windows Settings via ms-settings deep link.

    params:
      - target: a keyword like "display" or full "ms-settings:display"
    """
    target = (params.get("target") or "").strip().lower()
    if not target:
        target = "system"

    # Allow passing a full ms-settings URI
    if target.startswith("ms-settings:"):
        uri = target
    else:
        uri = SETTINGS_MAP.get(target, None)
        if uri is None:
            # Try a few normalizations
            uri = SETTINGS_MAP.get(target.replace("_", " ").replace("-", " "), "ms-settings:system")

    if os.name != "nt":
        return {"error": "settings.open is only supported on Windows."}

    before = _is_settings_running()

    # Open the Settings page
    # start "" "ms-settings:display"
    subprocess.Popen(["cmd", "/c", "start", "", uri], shell=False)

    time.sleep(0.5)
    after = _is_settings_running()

    return {
        "result": {
            "opened": True,
            "uri": uri,
            "settings_running_before": before,
            "settings_running_after": after,
        }
    }
