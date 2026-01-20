# runner/tools_power_timeouts.py
from __future__ import annotations

from typing import Dict, Any, Optional, Tuple
import subprocess
import re
import tempfile
from pathlib import Path
from datetime import datetime


# -------------------------
# Helpers
# -------------------------
def _run_cmd(args: list[str]) -> Dict[str, Any]:
    """
    Run a command and return {"ok": bool, "stdout": str, "stderr": str, "code": int}.
    """
    try:
        cp = subprocess.run(args, capture_output=True, text=True, shell=False)
        return {
            "ok": cp.returncode == 0,
            "stdout": (cp.stdout or "").strip(),
            "stderr": (cp.stderr or "").strip(),
            "code": cp.returncode,
        }
    except Exception as e:
        return {"ok": False, "stdout": "", "stderr": str(e), "code": -1}


def _parse_powercfg_indexes(output: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Parse:
      Current AC Power Setting Index: 0x...
      Current DC Power Setting Index: 0x...
    Returns (ac_value_int, dc_value_int)
    """
    ac = None
    dc = None

    # Handles both 0x00000032 and longer 0x0000000000000032 forms
    m_ac = re.search(r"Current\s+AC\s+Power\s+Setting\s+Index:\s*(0x[0-9a-fA-F]+)", output)
    m_dc = re.search(r"Current\s+DC\s+Power\s+Setting\s+Index:\s*(0x[0-9a-fA-F]+)", output)

    if m_ac:
        try:
            ac = int(m_ac.group(1), 16)
        except ValueError:
            ac = None

    if m_dc:
        try:
            dc = int(m_dc.group(1), 16)
        except ValueError:
            dc = None

    return ac, dc


def _minutes_from_seconds(sec: Optional[int]) -> Optional[int]:
    if sec is None:
        return None
    return int(sec // 60)


def _seconds_from_minutes(minutes: int) -> int:
    return int(minutes) * 60


def _powercfg_query(setting_alias_group: str, setting_alias: str) -> Dict[str, Any]:
    """
    powercfg /query SCHEME_CURRENT <GROUP> <SETTING>
    Fallback: powercfg /qh SCHEME_CURRENT <GROUP> <SETTING> (more verbose, often includes AC/DC indexes)
    Returns:
      {"ok": True, "ac": int|None, "dc": int|None, "raw": str, "source": "query"|"qh"}
      or {"ok": False, "error": str, "raw": str, ...}
    """
    res = _run_cmd(["powercfg", "/query", "SCHEME_CURRENT", setting_alias_group, setting_alias])
    if not res["ok"]:
        return {
            "ok": False,
            "error": res["stderr"] or f"powercfg failed (code {res['code']})",
            "raw": res["stdout"],
        }

    ac, dc = _parse_powercfg_indexes(res["stdout"])
    if ac is None and dc is None:
        # Fallback to /qh (this is what fixed EPP for you)
        qh = _run_cmd(["powercfg", "/qh", "SCHEME_CURRENT", setting_alias_group, setting_alias])
        if qh["ok"]:
            ac2, dc2 = _parse_powercfg_indexes(qh["stdout"])
            if ac2 is not None or dc2 is not None:
                return {"ok": True, "ac": ac2, "dc": dc2, "raw": qh["stdout"], "source": "qh"}

            return {
                "ok": False,
                "error": "Could not parse AC/DC indexes from powercfg /qh output for this setting",
                "raw": qh["stdout"],
                "source": "qh",
            }

        return {
            "ok": False,
            "error": "powercfg output did not include AC/DC indexes for this setting (and /qh fallback failed)",
            "raw": res["stdout"],
            "qh_error": qh["stderr"] or f"powercfg /qh failed (code {qh['code']})",
        }

    return {"ok": True, "ac": ac, "dc": dc, "raw": res["stdout"], "source": "query"}



# -------------------------
# Timeouts (Screen / Sleep / Hibernate)
# -------------------------
def power_get_timeouts(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Returns timeouts in minutes for:
      - screen (monitor timeout) AC/DC
      - sleep (standby timeout) AC/DC
      - hibernate timeout AC/DC
    """
    screen = _powercfg_query("SUB_VIDEO", "VIDEOIDLE")          # seconds
    sleep = _powercfg_query("SUB_SLEEP", "STANDBYIDLE")         # seconds
    hib = _powercfg_query("SUB_SLEEP", "HIBERNATEIDLE")         # seconds

    if not (screen.get("ok") and sleep.get("ok") and hib.get("ok")):
        return {
            "result": {
                "ok": False,
                "error": "Failed to read one or more timeout values.",
                "debug": {"screen": screen, "sleep": sleep, "hibernate": hib},
            }
        }

    result = {
        "ok": True,
        "screen": {
            "ac_minutes": _minutes_from_seconds(screen["ac"]),
            "dc_minutes": _minutes_from_seconds(screen["dc"]),
            "raw_seconds": {"ac": screen["ac"], "dc": screen["dc"]},
        },
        "sleep": {
            "ac_minutes": _minutes_from_seconds(sleep["ac"]),
            "dc_minutes": _minutes_from_seconds(sleep["dc"]),
            "raw_seconds": {"ac": sleep["ac"], "dc": sleep["dc"]},
        },
        "hibernate": {
            "ac_minutes": _minutes_from_seconds(hib["ac"]),
            "dc_minutes": _minutes_from_seconds(hib["dc"]),
            "raw_seconds": {"ac": hib["ac"], "dc": hib["dc"]},
        },
        "note": "Timeouts are stored in seconds internally; we return minutes for convenience.",
    }
    return {"result": result}


def _apply_ac_dc_minutes(which: str, minutes: int, apply_to: str) -> Dict[str, Any]:
    """
    Use: powercfg -change -<which>-timeout-ac/minutes and -dc.
    which: monitor | standby | hibernate
    apply_to: 'ac' | 'dc' | 'both'
    """
    apply_to = (apply_to or "both").lower()

    cmds = []
    if apply_to in ("ac", "both"):
        cmds.append(["powercfg", "-change", f"-{which}-timeout-ac", str(int(minutes))])
    if apply_to in ("dc", "both"):
        cmds.append(["powercfg", "-change", f"-{which}-timeout-dc", str(int(minutes))])

    errors = []
    for c in cmds:
        res = _run_cmd(c)
        if not res["ok"]:
            errors.append({"cmd": " ".join(c), "stderr": res["stderr"], "code": res["code"]})

    return {"ok": len(errors) == 0, "errors": errors or None}


def power_set_sleep_timeout(params: Dict[str, Any]) -> Dict[str, Any]:
    minutes = int(params.get("minutes", 0))
    apply_to = (params.get("apply_to") or "both").lower()

    before = power_get_timeouts({})["result"]
    applied = _apply_ac_dc_minutes("standby", minutes, apply_to)
    after = power_get_timeouts({})["result"]

    return {
        "result": {
            "ok": applied["ok"],
            "requested": {"minutes": minutes, "apply_to": apply_to},
            "before": before,
            "after": after,
            "errors": applied["errors"],
        }
    }


def power_set_screen_timeout(params: Dict[str, Any]) -> Dict[str, Any]:
    minutes = int(params.get("minutes", 0))
    apply_to = (params.get("apply_to") or "both").lower()

    before = power_get_timeouts({})["result"]
    applied = _apply_ac_dc_minutes("monitor", minutes, apply_to)
    after = power_get_timeouts({})["result"]

    return {
        "result": {
            "ok": applied["ok"],
            "requested": {"minutes": minutes, "apply_to": apply_to},
            "before": before,
            "after": after,
            "errors": applied["errors"],
        }
    }


def power_set_hibernate_timeout(params: Dict[str, Any]) -> Dict[str, Any]:
    minutes = int(params.get("minutes", 0))
    apply_to = (params.get("apply_to") or "both").lower()

    before = power_get_timeouts({})["result"]
    applied = _apply_ac_dc_minutes("hibernate", minutes, apply_to)
    after = power_get_timeouts({})["result"]

    return {
        "result": {
            "ok": applied["ok"],
            "requested": {"minutes": minutes, "apply_to": apply_to},
            "before": before,
            "after": after,
            "errors": applied["errors"],
        }
    }


# -------------------------
# Hibernate on/off + status
# -------------------------
def power_hibernate_status(params: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_cmd(["powercfg", "/a"])
    if not res["ok"]:
        return {"result": {"ok": False, "error": res["stderr"] or "powercfg /a failed"}}

    txt = res["stdout"]
    # best-effort detection
    hibernate_available = "Hibernate" in txt and "The following sleep states are available" in txt
    hibernate_disabled = "Hibernation has not been enabled" in txt or "Hibernate has not been enabled" in txt

    return {
        "result": {
            "ok": True,
            "hibernate_available": bool(hibernate_available) and not bool(hibernate_disabled),
            "raw": txt,
        }
    }


def power_hibernate_on(params: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_cmd(["powercfg", "/hibernate", "on"])
    after = power_hibernate_status({})["result"]
    return {"result": {"ok": res["ok"], "after": after, "error": res["stderr"] or None}}


def power_hibernate_off(params: Dict[str, Any]) -> Dict[str, Any]:
    res = _run_cmd(["powercfg", "/hibernate", "off"])
    after = power_hibernate_status({})["result"]
    return {"result": {"ok": res["ok"], "after": after, "error": res["stderr"] or None}}


# -------------------------
# Energy saver (Battery Saver) controls
# -------------------------
def power_energy_saver_status(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reads the Energy Saver (Battery Saver) threshold from the active scheme.
    Uses powercfg aliases:
      SUB_ENERGYSAVER / ESBATTTHRESHOLD
    """
    q = _powercfg_query("SUB_ENERGYSAVER", "ESBATTTHRESHOLD")  # value is percent (0-100)
    if not q.get("ok"):
        return {"result": {"ok": False, "error": q.get("error"), "debug": q}}

    ac = q.get("ac")
    dc = q.get("dc")

    def _classify(v: Optional[int]) -> Optional[str]:
        if v is None:
            return None
        if v >= 100:
            return "always_on"
        if v == 0:
            return "never"
        if v >= 70:
            return "best_power_efficiency-ish"
        if v >= 40:
            return "balanced-ish"
        return "best_performance-ish"

    return {
        "result": {
            "ok": True,
            "plugged_in_threshold_percent": ac,
            "on_battery_threshold_percent": dc,
            "plugged_in_state": _classify(ac),
            "on_battery_state": _classify(dc),
            "note": "Energy Saver is controlled by a battery threshold within the active plan (ESBATTTHRESHOLD).",
        }
    }


def power_energy_saver_threshold(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Set Energy Saver threshold percent (0-100) for AC/DC/both.
    - 0   => never
    - 100 => effectively 'always on' (turns on immediately when on battery)
    """
    percent = int(params.get("percent", 0))
    percent = max(0, min(100, percent))
    apply_to = (params.get("apply_to") or "both").lower()

    before = power_energy_saver_status({})["result"]

    errors = []

    if apply_to in ("ac", "both"):
        r = _run_cmd(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", "SUB_ENERGYSAVER", "ESBATTTHRESHOLD", str(percent)])
        if not r["ok"]:
            errors.append({"cmd": "setacvalueindex", "stderr": r["stderr"], "code": r["code"]})

    if apply_to in ("dc", "both"):
        r = _run_cmd(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", "SUB_ENERGYSAVER", "ESBATTTHRESHOLD", str(percent)])
        if not r["ok"]:
            errors.append({"cmd": "setdcvalueindex", "stderr": r["stderr"], "code": r["code"]})

    # Re-activate current scheme to apply immediately
    _run_cmd(["powercfg", "/setactive", "SCHEME_CURRENT"])

    after = power_energy_saver_status({})["result"]

    return {
        "result": {
            "ok": len(errors) == 0,
            "requested": {"percent": percent, "apply_to": apply_to},
            "before": before,
            "after": after,
            "errors": errors or None,
        }
    }


def power_energy_saver_on(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Practical "ON": set threshold to 100% so it activates immediately (especially on battery),
    then re-activate scheme.
    """
    apply_to = (params.get("apply_to") or "both").lower()
    params2 = {"percent": 100, "apply_to": apply_to}
    return power_energy_saver_threshold(params2)


def power_energy_saver_off(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Practical "OFF": set threshold to 0% (never), then re-activate scheme.
    """
    apply_to = (params.get("apply_to") or "both").lower()
    params2 = {"percent": 0, "apply_to": apply_to}
    return power_energy_saver_threshold(params2)


# -------------------------
# Battery usage (v0 helpers)
# -------------------------
def power_battery_report(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Generate a Windows battery report HTML file via powercfg /batteryreport.
    """
    days = int(params.get("days", 7))
    days = max(1, min(365, days))

    out_dir = Path(tempfile.gettempdir()) / "jarvis_battery_reports"
    out_dir.mkdir(parents=True, exist_ok=True)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_file = out_dir / f"battery_report_{ts}.html"

    res = _run_cmd(["powercfg", "/batteryreport", "/output", str(out_file), "/duration", str(days)])
    if not res["ok"]:
        return {"result": {"ok": False, "error": res["stderr"] or "batteryreport failed", "raw": res["stdout"]}}

    return {"result": {"ok": True, "path": str(out_file), "days": days}}


def power_open_battery_usage(params: Dict[str, Any]) -> Dict[str, Any]:
    """
    Open Battery usage page.
    """
    # This URI is commonly available on Win11; if it fails, Settings will just not open.
    ps = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Start-Process 'ms-settings:batterysaver-usagedetails'"
    ]
    res = _run_cmd(ps)
    return {"result": {"ok": res["ok"], "error": res["stderr"] or None}}
