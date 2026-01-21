from __future__ import annotations
from typing import Any, Dict, Tuple, List, Optional
import os
import re
import json
import subprocess

SUB_PROCESSOR = "54533251-82be-4824-96c1-47b60b740d00"
EPP_GUID      = "36687f9e-e3a5-4dbf-b1dc-15eb381c6863"

EPP_PRESETS = {
    "best_performance": 0,
    "balanced": 50,
    "best_power_efficiency": 80,
}


def _run(cmd: List[str]) -> Tuple[int, str, str]:
    p = subprocess.run(cmd, capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _run_powershell(script: str) -> Tuple[int, str, str]:
    p = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
        capture_output=True,
        text=True,
    )
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _parse_powercfg_list(output: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
    """
    Parse: powercfg /l
    Returns (schemes, active_guid)
    """
    schemes: List[Dict[str, Any]] = []
    active_guid: Optional[str] = None

    # Typical line:
    # Power Scheme GUID: xxxx-xxxx-...  (Balanced) *
    rx = re.compile(r"Power Scheme GUID:\s*([a-fA-F0-9\-]{36})\s*\((.+?)\)\s*(\*)?")

    for line in output.splitlines():
        m = rx.search(line)
        if not m:
            continue
        guid = m.group(1).lower()
        name = m.group(2).strip()
        is_active = bool(m.group(3))
        schemes.append({"guid": guid, "name": name, "active": is_active})
        if is_active:
            active_guid = guid

    return schemes, active_guid


def power_list_schemes(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "power.list_schemes is only implemented on Windows right now."}

    code, out, err = _run(["powercfg", "/l"])
    if code != 0:
        return {"result": {"ok": False, "error": err or out or "powercfg failed"}}

    schemes, active_guid = _parse_powercfg_list(out)
    return {
        "result": {
            "ok": True,
            "count": len(schemes),
            "active_guid": active_guid,
            "schemes": schemes,
        }
    }


def power_get_state(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "power.get_state is only implemented on Windows right now."}

    schemes_resp = power_list_schemes({})
    schemes = (schemes_resp.get("result") or {}).get("schemes", [])
    active_guid = (schemes_resp.get("result") or {}).get("active_guid")

    active_name = None
    for s in schemes:
        if s.get("guid") == active_guid:
            active_name = s.get("name")
            break

    # Battery info (best effort — desktops often return nothing)
    ps = (
        "try { "
        "$b = Get-CimInstance -ClassName Win32_Battery -ErrorAction Stop "
        "| Select-Object EstimatedChargeRemaining, BatteryStatus "
        "| ConvertTo-Json -Depth 3; "
        "Write-Output $b "
        "} catch { "
        "Write-Output (ConvertTo-Json @{ supported=$false; error=$_.Exception.Message }) "
        "}"
    )
    _, bout, _ = _run_powershell(ps)

    battery = {"supported": False, "percent": None, "status": None, "error": None}
    try:
        bdata = json.loads(bout) if bout else None
        if isinstance(bdata, dict) and bdata.get("supported") is False:
            battery["error"] = bdata.get("error")
        elif isinstance(bdata, list) and bdata:
            b0 = bdata[0]
            battery["supported"] = True
            battery["percent"] = b0.get("EstimatedChargeRemaining")
            battery["status"] = b0.get("BatteryStatus")
        elif isinstance(bdata, dict) and bdata:
            battery["supported"] = True
            battery["percent"] = bdata.get("EstimatedChargeRemaining")
            battery["status"] = bdata.get("BatteryStatus")
    except Exception:
        # ignore parse errors
        pass

    return {
        "result": {
            "ok": True,
            "active": {"guid": active_guid, "name": active_name},
            "schemes": schemes,
            "battery": battery,
            "note": "Battery info may be unsupported on desktops.",
        }
    }


def power_set_scheme(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "power.set_scheme is only implemented on Windows right now."}

    name = (params.get("name") or "").strip()
    guid = (params.get("guid") or "").strip().lower()

    schemes_resp = power_list_schemes({})
    schemes = (schemes_resp.get("result") or {}).get("schemes", [])
    before = power_get_state({}).get("result", {})

    target_guid = None

    if guid:
        # validate guid in list
        for s in schemes:
            if s.get("guid") == guid:
                target_guid = guid
                break
        if not target_guid:
            return {"result": {"ok": False, "error": f"Unknown power scheme GUID: {guid}", "before": before}}

    if not target_guid:
        if not name:
            return {"error": "Missing param 'name' (e.g. 'balanced') or 'guid'."}

        # fuzzy match by name substring
        low = name.lower()
        for s in schemes:
            if low in (s.get("name", "").lower()):
                target_guid = s.get("guid")
                break

        # common aliases
        if not target_guid:
            aliases = {
                "balanced": "balanced",
                "high performance": "high performance",
                "performance": "high performance",
                "power saver": "power saver",
                "battery saver": "power saver",
                "save power": "power saver",
            }
            mapped = aliases.get(low)
            if mapped:
                for s in schemes:
                    if mapped in (s.get("name", "").lower()):
                        target_guid = s.get("guid")
                        break

    if not target_guid:
        return {"result": {"ok": False, "error": f"Could not find a scheme matching '{name}'", "before": before}}

    code, out, err = _run(["powercfg", "/setactive", target_guid])
    after = power_get_state({}).get("result", {})

    return {
        "result": {
            "ok": code == 0,
            "requested": {"name": name or None, "guid": target_guid},
            "before": before.get("active"),
            "after": after.get("active"),
            "error": err or None,
            "raw": out or None,
        }
    }

def _read_epp(ac: bool) -> Dict[str, Any]:
    # /qh includes hidden settings; we must parse the *specific* EPP block
    code, out, err = _run(["powercfg", "/qh", "SCHEME_CURRENT"])
    if code != 0 or not out:
        return {"ok": False, "error": err or out or "powercfg /qh failed", "raw": (out or err or "")[:1200]}

    text = out

    # Find the EPP GUID occurrence (case-insensitive)
    idx = text.lower().find(EPP_GUID.lower())
    if idx == -1:
        return {"ok": False, "error": "EPP GUID not found in powercfg /qh output", "raw": text[:1200]}

    # Find start of the containing setting block
    # Blocks look like: "Power Setting GUID: <guid>  (Name)"
    block_start = text.rfind("Power Setting GUID:", 0, idx)
    if block_start == -1:
        # fallback: start a bit before idx
        block_start = max(0, idx - 2000)

    # Find end of this setting block (next "Power Setting GUID:")
    next_block = text.find("Power Setting GUID:", idx + 1)
    block_end = next_block if next_block != -1 else len(text)

    block = text[block_start:block_end]

    # Now parse current AC/DC only *inside this block*
    if ac:
        pats = [
            r"Current\s+AC\s+Power\s+Setting\s+Index:\s*0x([0-9a-fA-F]+)",
            r"Current\s+AC.*?:\s*0x([0-9a-fA-F]+)",
        ]
    else:
        pats = [
            r"Current\s+DC\s+Power\s+Setting\s+Index:\s*0x([0-9a-fA-F]+)",
            r"Current\s+DC.*?:\s*0x([0-9a-fA-F]+)",
        ]

    for pat in pats:
        m = re.search(pat, block, flags=re.IGNORECASE)
        if m:
            return {"ok": True, "value": int(m.group(1), 16)}

    return {
        "ok": False,
        "error": f"Could not parse EPP current index from EPP block ({'AC' if ac else 'DC'})",
        "raw": block[:1200],
    }


def _epp_to_mode(v: int) -> str:
    # rough mapping to match Windows UI intent
    if v <= 15:
        return "best_performance"
    if v >= 70:
        return "best_power_efficiency"
    return "balanced"


def power_get_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "power.get_mode is only implemented on Windows right now."}

    ac = _read_epp(ac=True)
    dc = _read_epp(ac=False)

    # If both failed, include raw snippets to debug
    if not ac.get("ok") and not dc.get("ok"):
        return {
            "result": {
                "ok": False,
                "error": ac.get("error") or dc.get("error"),
                "debug": {
                    "ac": {"error": ac.get("error"), "raw": ac.get("raw")},
                    "dc": {"error": dc.get("error"), "raw": dc.get("raw")},
                }
            }
        }

    out = {"ok": True, "plugged_in": None, "on_battery": None, "raw": {}}

    if ac.get("ok"):
        out["raw"]["ac_epp"] = ac["value"]
        out["plugged_in"] = _epp_to_mode(ac["value"])
    else:
        out["raw"]["ac_error"] = ac.get("error")

    if dc.get("ok"):
        out["raw"]["dc_epp"] = dc["value"]
        out["on_battery"] = _epp_to_mode(dc["value"])
    else:
        out["raw"]["dc_error"] = dc.get("error")

    out["note"] = "Power Mode maps to CPU Energy Performance Preference (EPP) within the active plan."
    return {"result": out}


def power_set_mode(params: Dict[str, Any]) -> Dict[str, Any]:
    if os.name != "nt":
        return {"error": "power.set_mode is only implemented on Windows right now."}

    mode = (params.get("mode") or "").strip().lower()
    apply_to = (params.get("apply_to") or "both").strip().lower()  # both|ac|dc

    # friendly aliases
    aliases = {
        "best performance": "best_performance",
        "performance": "best_performance",
        "best_performance": "best_performance",
        "balanced": "balanced",
        "best power efficiency": "best_power_efficiency",
        "efficiency": "best_power_efficiency",
        "best_power_efficiency": "best_power_efficiency",
        "battery saver": "best_power_efficiency",
        "power saver": "best_power_efficiency",
    }
    mode = aliases.get(mode, mode)

    if mode not in EPP_PRESETS:
        return {"error": "Missing/invalid 'mode'. Use: best_power_efficiency | balanced | best_performance"}

    val = EPP_PRESETS[mode]
    before = power_get_mode({}).get("result", {})

    errors = []
    ok_any = False

    if apply_to in ("both", "ac"):
        c, out, err = _run(["powercfg", "/setacvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, EPP_GUID, str(val)])
        if c == 0:
            ok_any = True
        else:
            errors.append(err or out or "setacvalueindex failed")

    if apply_to in ("both", "dc"):
        c, out, err = _run(["powercfg", "/setdcvalueindex", "SCHEME_CURRENT", SUB_PROCESSOR, EPP_GUID, str(val)])
        if c == 0:
            ok_any = True
        else:
            errors.append(err or out or "setdcvalueindex failed")

    # Apply changes
    _run(["powercfg", "/setactive", "SCHEME_CURRENT"])

    after = power_get_mode({}).get("result", {})

    return {
        "result": {
            "ok": ok_any and not errors,
            "requested": {"mode": mode, "epp": val, "apply_to": apply_to},
            "before": before,
            "after": after,
            "errors": errors or None,
        }
    }

