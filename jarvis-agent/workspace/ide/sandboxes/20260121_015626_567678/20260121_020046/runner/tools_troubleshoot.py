# runner/tools_troubleshoot.py
from __future__ import annotations
from typing import Any, Dict, Optional
import os
import shutil
import subprocess

# Best-effort mapping (varies by Windows build)
# If a key isn't found, user can pass an explicit diag id.
TROUBLESHOOTER_IDS = {
    "audio": "AudioPlaybackDiagnostic",
    "network": "NetworkDiagnosticsNetworkAdapter",
    "internet": "NetworkDiagnosticsWeb",
    "printer": "PrinterDiagnostic",
    "windows update": "WindowsUpdateDiagnostic",
    "bluetooth": "BluetoothDiagnostic",
    "camera": "CameraDiagnostic",
    "program compatibility": "PCWDiagnostic",
    "video playback": "VideoPlaybackDiagnostic",
    "windows media player": "WindowsMediaPlayerDiagnostic",
    "bits": "BITSDiagnostic",
}

def _is_windows() -> bool:
    return os.name == "nt"

def _msdt_path() -> Optional[str]:
    # msdt.exe is being deprecated/removed on newer Windows
    if not _is_windows():
        return None
    p = shutil.which("msdt")
    return p

def troubleshoot_list(params: Dict[str, Any]) -> Dict[str, Any]:
    if not _is_windows():
        return {"result": {"supported": False}}

    msdt = _msdt_path()
    return {
        "result": {
            "supported": True,
            "msdt_available": bool(msdt),
            "known_troubleshooters": sorted(TROUBLESHOOTER_IDS.keys()),
            "note": "On newer Windows 11 builds, troubleshooters may redirect to Get Help and msdt may be unavailable.",
        }
    }

def troubleshoot_open_settings(params: Dict[str, Any]) -> Dict[str, Any]:
    if not _is_windows():
        return {"result": {"supported": False}}

    # Opens: Settings → System → Troubleshoot
    subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:troubleshoot"], shell=False)
    return {"result": {"supported": True, "opened": True, "target": "ms-settings:troubleshoot"}}

def troubleshoot_run(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Run a Windows troubleshooter (best-effort).
    Prefers legacy msdt.exe /id <DiagnosticID> if available, otherwise opens Settings troubleshooter page.
    """
    if not _is_windows():
        return {"result": {"supported": False}}

    name = (params.get("name") or params.get("troubleshooter") or "").strip().lower()
    diag_id = (params.get("id") or params.get("diag_id") or "").strip()

    if not diag_id:
        if not name:
            raise ValueError("Missing 'name' (e.g. 'audio') or explicit 'id' (e.g. 'AudioPlaybackDiagnostic').")
        diag_id = TROUBLESHOOTER_IDS.get(name)

    if not diag_id:
        raise ValueError(f"Unknown troubleshooter '{name}'. Try: troubleshoot list, or pass an explicit id.")

    msdt = _msdt_path()
    if msdt:
        # Launch interactive troubleshooter UI
        subprocess.Popen([msdt, "/id", diag_id], shell=False)
        return {"result": {"supported": True, "launched": True, "method": "msdt", "id": diag_id}}

    # Fallback if msdt is missing/removed
    subprocess.Popen(["cmd", "/c", "start", "", "ms-settings:troubleshoot"], shell=False)
    return {
        "result": {
            "supported": True,
            "launched": False,
            "method": "settings_fallback",
            "id": diag_id,
            "note": "msdt.exe not available (newer Windows). Opened Settings → Troubleshoot instead.",
        }
    }
