# agent/core.py
from typing import Dict, Any, Optional
from datetime import datetime, timedelta
import re
import difflib
import json
import concurrent.futures

from .policy import Policy
from .tools import TOOLS
from .safety import should_confirm, log_action, Tool
from .models import load_model_roles


_policy = Policy.load()

_general_model, _coder_model, _research_model = load_model_roles()


# If Jarvis suggests a command, store it here so "yes" can execute it.
_PENDING_SUGGESTION: Optional[str] = None

# Limit LLM tool-router so it never hangs the CLI
LLM_ROUTER_TIMEOUT_SECONDS = 6
LLM_ROUTER_ENABLED = True


def _format_tool_output(tool_name: str, out: Any) -> Optional[str]:
    """
    Return a nice human-readable string for specific tools.
    If None, core will fall back to the default raw output.
    """
    if out is None:
        return None

    # Common structure: {"result": {...}} or {"error": "..."}
    if isinstance(out, dict) and "error" in out:
        return f"Jarvis: ❌ {out['error']}"

    result = out.get("result") if isinstance(out, dict) else None
    if result is None:
        return None

    # -------- logs.* --------
    if tool_name == "logs.list":
        logs = result.get("logs", [])
        if not logs:
            return "Jarvis: No audit logs found yet."
        lines = ["Jarvis: Recent audit logs:"]
        for i, item in enumerate(logs, start=1):
            name = item.get("name", "?")
            mod = item.get("modified", "?")
            size = item.get("size_bytes", 0)
            kb = round(size / 1024, 1)
            lines.append(f"  {i}. {name}  ({mod}, {kb} KB)")
        return "\n".join(lines)

    if tool_name in ("logs.last", "logs.tail"):
        file_ = result.get("file", "?")
        lines_list = result.get("lines", [])
        if not lines_list:
            return f"Jarvis: {file_} is empty."
        header = f"Jarvis: Tail of {file_} ({len(lines_list)} lines):"
        body = "\n".join(f"  {ln}" for ln in lines_list)
        return f"{header}\n{body}"

    if tool_name == "logs.summarize_tail":
        file_ = result.get("file", "?")
        summary = result.get("summary", {})
        counts = summary.get("counts", {})
        notes = summary.get("notes", [])
        top = summary.get("top_planned_tools", [])
        preview = result.get("tail_preview", [])

        lines = [f"Jarvis: Log summary for {file_}:"]
        lines.append("  Counts:")
        lines.append(f"    - tracebacks: {counts.get('traceback', 0)}")
        lines.append(f"    - errors: {counts.get('error', 0)}")
        lines.append(f"    - exceptions: {counts.get('exception', 0)}")
        lines.append(f"    - policy blocks: {counts.get('policy_blocks', 0)}")
        lines.append(f"    - tool calls: {counts.get('tool_calls', 0)}")

        if top:
            lines.append("  Top planned tools:")
            for item in top:
                lines.append(f"    - {item.get('tool')} ({item.get('count')})")

        if notes:
            lines.append("  Notes:")
            for n in notes:
                lines.append(f"    - {n}")

        if preview:
            lines.append("  Tail preview:")
            for ln in preview:
                lines.append(f"    {ln}")

        return "\n".join(lines)

    # -------- code.* --------
    if tool_name == "code.read_file":
        path = result.get("path", "?")
        lines_list = result.get("lines", [])
        if not lines_list:
            return f"Jarvis: File {path} is empty."
        body = "\n".join(lines_list)
        return f"Jarvis: {path}\n\n{body}"

    if tool_name == "code.search":
        query = result.get("query", "?")
        path = result.get("path", "?")
        matches = result.get("matches", [])
        scanned = result.get("files_scanned", 0)

        if not matches:
            return f"Jarvis: No matches for '{query}' in {path} (scanned {scanned} files)."

        # group by file
        grouped: Dict[str, list] = {}
        for m in matches:
            grouped.setdefault(m.get("file", "?"), []).append(m)

        lines = [f"Jarvis: Search results for '{query}' in {path} (scanned {scanned} files):"]
        for file_, ms in grouped.items():
            lines.append(f"  {file_}:")
            for m in ms[:20]:
                ln = m.get("line", "?")
                txt = m.get("text", "")
                lines.append(f"    - line {ln}: {txt}")
        return "\n".join(lines)

    return None


# -------------------------
# Normalization + Aliases
# -------------------------
def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[?!.,;:]+$", "", text)
    return " ".join(text.split())


# -------------------------
# Universal alias engine
# -------------------------

GLOBAL_REPLACEMENTS: list[tuple[str, str]] = [
    # devices / system
    (r"\bpc\b", "system"),
    (r"\bcomputer\b", "system"),
    (r"\bdevice\b", "system"),

    # common short forms
    (r"\bbt\b", "bluetooth"),
    (r"\bwi[-\s]?fi\b", "wifi"),
    (r"\bui\s*automation\b", "uia"),

    # status/state
    (r"\bstate\b", "status"),

    # on/off verbs (keeps meaning)
    (r"\bturn\s+on\b", "on"),
    (r"\bswitch\s+on\b", "on"),
    (r"\benable\b", "on"),
    (r"\bturn\s+off\b", "off"),
    (r"\bswitch\s+off\b", "off"),
    (r"\bdisable\b", "off"),

    # optional politeness/filler removal
    (r"\bplease\b", ""),
    (r"\bkindly\b", ""),
    (r"\bcan\s+you\b", ""),
    (r"\bcould\s+you\b", ""),

    # "set X to Y" → "X Y" (helps tons of patterns)
    (r"^set\s+(.+?)\s+to\s+(.+)$", r"\1 \2"),
]


def _apply_global_replacements(normalized: str) -> str:
    out = normalized
    for pattern, repl in GLOBAL_REPLACEMENTS:
        out = re.sub(pattern, repl, out)
    out = " ".join(out.split())
    return out


def _auto_alias(normalized: str) -> str:
    """
    Convert common natural phrasings into the canonical commands your handlers expect.
    This is where "aliases for everything" actually happens.
    """
    s = _apply_global_replacements(normalized)

    # ---- status/state normalization ----
    # Most of your handlers use "<thing> status" or "<thing> state".
    # We unify a bunch of common variants.
    status_map = {
        "system status": "system info",
        "system info status": "system info",
        "storage status": "storage",
        "disk status": "storage",
        "drive status": "storage",

        "display status": "display status",
        "screen status": "display status",

        "audio status": "audio status",
        "sound status": "audio status",
        "volume status": "audio status",

        "bluetooth status": "bluetooth status",
        "wifi status": "network status",
        "network status": "network status",

        "power status": "power status",
        "battery status": "power status",

        "runner status": "runner is elevated",
        "runner elevated status": "runner is elevated",
        "uia status": "uia status",

        "vision status": "accessibility vision status",
        "accessibility status": "accessibility vision status",
    }
    if s in status_map:
        return status_map[s]

    # ---- wifi/network common language ----
    if s in ("wifi scan", "wifi networks", "nearby wifi", "list wifi", "list wifi networks"):
        return "scan wifi"
    if s in ("wifi scan detailed", "scan wifi detailed"):
        return "scan wifi detailed"

    # ---- on/off patterns that people naturally type ----
    # (global replacements already changed enable/disable/turn on/off)
    # We just align the "wifi on/off", "bluetooth on/off", etc.
    if s == "wifi on":
        return "wifi on"
    if s == "wifi off":
        return "wifi off"
    if s == "bluetooth on":
        return "bluetooth on"
    if s == "bluetooth off":
        return "bluetooth off"

    # Energy saver synonyms
    if s == "battery saver on":
        return "energy saver on"
    if s == "battery saver off":
        return "energy saver off"
    if s == "battery saver status":
        return "energy saver status"

    # ---- display scale synonyms ----
    if s in ("scale status", "scaling status", "display scale status"):
        return "display status"

    # Default: return cleaned phrase
    return s


def _resolve_command(raw: str) -> str:
    """
    One place to normalize -> auto-alias -> manual ALIASES override.
    Manual ALIASES wins at the end so you can force anything.
    """
    n = _normalize(raw)
    n = _auto_alias(n)
    return ALIASES.get(n, n)


ALIASES = {
    # runner
    "runner elevated": "runner is elevated",
    "runner admin": "runner is elevated",
    "runner status": "runner is elevated",
    "runner elevated?": "runner is elevated",
    "is runner elevated": "runner is elevated",
    "is runner elevated?": "runner is elevated",

    # wifi
    "wifi enable": "wifi on",
    "enable wifi": "wifi on",
    "turn on wifi": "wifi on",
    "wifi disable": "wifi off",
    "disable wifi": "wifi off",
    "turn off wifi": "wifi off",

    "wifi scan": "scan wifi",
    "scan wifi": "scan wifi",
    "nearby wifi": "scan wifi",
    "wifi networks": "scan wifi",
    "list wifi": "scan wifi",
    "list wifi networks": "scan wifi",
    "scan wifi detailed": "scan wifi detailed",
    "wifi scan detailed": "scan wifi detailed",

    # uia
    "uia": "uia status",
    "uia state": "uia status",
    "ui automation": "uia status",
    "ui automation status": "uia status",


    # settings
    "settings wifi": "open settings wifi",
    "settings bluetooth": "open settings bluetooth",
    "settings display": "open settings display",
    "settings update": "open settings windows update",
    "windows update": "open settings windows update",

    # bluetooth
    "paired devices": "list paired devices",
    "bluetooth paired devices": "list paired devices",
    "list bluetooth devices": "list paired devices",

    # power/battery shortcuts
    "power timeouts": "power get timeouts",
    "timeouts": "power get timeouts",
    "battery usage per app": "srum report",
    "per app battery usage": "srum report",

    # ----- time & language (date & time) -----
    "time status": "date time status",
    "date & time status": "date time status",
    "date and time status": "date time status",

    "set time automatically on": "auto time on",
    "set time automatically off": "auto time off",
    "turn on set time automatically": "auto time on",
    "turn off set time automatically": "auto time off",
    "enable set time automatically": "auto time on",
    "disable set time automatically": "auto time off",

    "set time zone automatically on": "auto timezone on",
    "set time zone automatically off": "auto timezone off",
    "turn on set time zone automatically": "auto timezone on",
    "turn off set time zone automatically": "auto timezone off",
    "enable set time zone automatically": "auto timezone on",
    "disable set time zone automatically": "auto timezone off",

    "show time and date in the system tray on": "systray time on",
    "show time and date in the system tray off": "systray time off",
    "show time and date in system tray on": "systray time on",
    "show time and date in system tray off": "systray time off",

    "show time in notification center on": "notification time on",
    "show time in notification center off": "notification time off",

    "sync now": "sync time",
    "sync time now": "sync time",
    "resync now": "sync time",
    "resync time": "sync time",

    # gaming
    "game mode": "game mode status",
    "game mode status": "game mode status",
    "gaming mode": "game mode status",

    "game mode on": "game mode on",
    "turn on game mode": "game mode on",
    "enable game mode": "game mode on",

    "game mode off": "game mode off",
    "turn off game mode": "game mode off",
    "disable game mode": "game mode off",

    # accessibility (vision)
    "vision status": "accessibility vision status",
    "accessibility vision status": "accessibility vision status",

    "transparency effects on": "transparency effects on",
    "transparency effects off": "transparency effects off",
    "animation effects on": "animation effects on",
    "animation effects off": "animation effects off",

    "always show scrollbars on": "always show scrollbars on",
    "always show scrollbars off": "always show scrollbars off",



}


def _auto_alias(normalized: str) -> str:
    """
    Create canonical forms for commands so lots of phrasings work without
    manually listing every alias.
    """
    out = normalized

    # Apply global replacements
    for pattern, repl in GLOBAL_REPLACEMENTS:
        out = re.sub(pattern, repl, out)

    # normalize extra filler words
    out = re.sub(r"\bplease\b", "", out)
    out = re.sub(r"\bcan you\b", "", out)
    out = re.sub(r"\bcould you\b", "", out)
    out = re.sub(r"\bkindly\b", "", out)
    out = " ".join(out.split())

    # common "X status" patterns
    if out in ("system status",):
        return "system info"
    if out in ("storage status", "disk status", "drive status"):
        return "storage"
    if out in ("bluetooth status",):
        return "bluetooth status"
    if out in ("wifi status", "network status"):
        return "network status"
    if out in ("display status", "screen status"):
        return "display status"
    if out in ("audio status", "sound status", "volume status"):
        return "audio status"
    if out in ("uia status",):
        return "uia status"
    if out in ("runner status",):
        return "runner is elevated"

    # Make "brightness up/down" more robust
    out = out.replace("increase brightness", "brightness up")
    out = out.replace("decrease brightness", "brightness down")

    # Make "volume up/down" more robust
    out = out.replace("increase volume", "volume up")
    out = out.replace("decrease volume", "volume down")

    return out




KNOWN_COMMANDS = set(ALIASES.keys()) | set(ALIASES.values()) | {
    "help", "commands", "what can you do", "what can you do?",
    "system info", "my system", "pc info",
    "storage", "disk space", "drive space",
    "list installed apps", "installed apps", "apps list",

    "network status", "network state", "wifi status", "wifi state",
    "wifi on", "wifi off",
    "airplane mode", "open airplane mode",
    "airplane mode on", "turn airplane mode on",
    "airplane mode off", "turn airplane mode off",

    "uia status", "uia get status", "ui automation", "ui automation status", "uia",

    "runner is elevated", "runner elevated?", "runner admin", "runner status",
    "elevate runner", "runner elevate", "runner restart admin",

    "display state", "display status", "brightness status",
    "brightness up", "increase brightness",
    "brightness down", "decrease brightness",

    # Display MK2 additions
    "list displays", "display list", "list my displays",

    "bluetooth status", "bluetooth state", "bt status", "bt state",
    "bluetooth on", "turn bluetooth on", "enable bluetooth",
    "bluetooth off", "turn bluetooth off", "disable bluetooth",
    "list paired devices", "bluetooth paired", "list bluetooth",

    "audio status", "audio state", "volume status", "sound status",
    "volume up", "increase volume", "sound up",
    "volume down", "decrease volume", "sound down",
    "mute", "sound mute", "audio mute",
    "unmute", "sound unmute", "audio unmute",

    "list reminders", "show reminders", "show my reminders",
    "show activity", "show audit log", "show log",
    "clear reminders", "delete all reminders", "remove all reminders",

    "power get timeouts", "power timeouts",
    "hibernate status", "hibernate on", "hibernate off",
    "energy saver status", "energy saver on", "energy saver off",
    "set energy saver threshold",
    "battery usage", "open battery usage", "battery report",
    "srum report",

    "storage categories", "storage breakdown", "storage usage",
    "cleanup recommendations", "storage cleanup", "cleanup storage",

    "storage categories deep", "storage breakdown deep", "storage usage deep",
    "cleanup recommendations deep", "storage cleanup deep", "cleanup storage deep",

    "nearby sharing status", "nearby sharing state",
    "nearby sharing off", "nearby sharing my devices only", "nearby sharing everyone nearby",
    "rename nearby sharing", "set nearby sharing name",

    "multitasking status",
    "snap windows on/off",
    "title bar shake on/off",
    "alt tab tabs 3/5/20/off",


    
}


def _did_you_mean(normalized: str) -> Optional[str]:
    if not normalized or normalized in KNOWN_COMMANDS:
        return None
    matches = difflib.get_close_matches(normalized, list(KNOWN_COMMANDS), n=1, cutoff=0.78)
    return matches[0] if matches else None

def _resolve_command(raw: str) -> str:
    """
    Normalize + auto-alias + explicit alias mapping.
    """
    n = _normalize(raw)
    n = _auto_alias(n)

    # Explicit alias map wins last (so you can override auto behavior)
    return ALIASES.get(n, n)



def _run_tool(tool_name: str, params: Dict[str, Any]) -> Any:
    tool: Tool = TOOLS[tool_name]

    if should_confirm(tool, params):
        print(f"Jarvis: I plan to use '{tool.name}' with parameters: {params}")

        cfg = _policy.confirm_config()
        type_risks = set(cfg.get("type_to_confirm_for_risks", []) or [])
        phrase_high = cfg.get("type_phrase_high", "CONFIRM")
        phrase_critical = cfg.get("type_phrase_critical", "CONFIRM-CRITICAL")

        if tool.risk.name in type_risks:
            phrase = phrase_critical if tool.risk.name == "CRITICAL" else phrase_high
            typed = input(f"Type '{phrase}' to proceed: ").strip()
            if typed != phrase:
                print("Jarvis: Okay, I cancelled that action.")
                log_action(tool, params, "cancelled")
                return None
        else:
            choice = input("Proceed? (y/n): ").strip().lower()
            if choice not in ("y", "yes"):
                print("Jarvis: Okay, I cancelled that action.")
                log_action(tool, params, "cancelled")
                return None

    try:
        result = tool.func(params)
        log_action(tool, params, "success")

        if result is not None:
            pretty = _format_tool_output(tool_name, result)
            if pretty:
                print(pretty)
            else:
                print(f"Jarvis: Tool returned: {result}")

        return result

    except Exception as exc:
        print(f"Jarvis: Something went wrong while executing the tool: {exc}")
        log_action(tool, params, f"error: {exc}")
        return None




def _extract_when_from_text(text: str) -> str:
    lower = text.lower()
    now = datetime.now()

    m = re.search(r"at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?", lower)
    if m:
        hour = int(m.group(1))
        minute = int(m.group(2) or 0)
        ampm = m.group(3)

        if ampm:
            if ampm == "pm" and hour != 12:
                hour += 12
            if ampm == "am" and hour == 12:
                hour = 0

        day = now + timedelta(days=1) if "tomorrow" in lower else now
        dt = day.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return dt.strftime("%Y-%m-%d %H:%M")

    if "tomorrow" in lower:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")

    weekdays = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    for idx, name in enumerate(weekdays):
        if f"on {name}" in lower or lower.strip().startswith(name):
            days_ahead = (idx - now.weekday() + 7) % 7
            if days_ahead == 0:
                days_ahead = 7
            target = now + timedelta(days=days_ahead)
            return target.strftime("%Y-%m-%d")

    return "unspecified time"


# -------------------------
# LLM tool-routing fallback (safe)
# -------------------------
def _extract_first_json_object(text: str) -> Optional[dict]:
    if not text:
        return None
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    chunk = text[start:end + 1]
    try:
        return json.loads(chunk)
    except Exception:
        return None


def _tools_for_message(lower: str) -> Dict[str, Tool]:
    """
    Reduce tool catalog to a small relevant subset, so Phi-2 doesn't stall.
    """
    wanted: Dict[str, Tool] = {}

    def add(prefix: str):
        for k, t in TOOLS.items():
            if k.startswith(prefix):
                wanted[k] = t

    # Always allow these basics:
    for k in ("settings.open", "system.get_info", "system.get_storage"):
        if k in TOOLS:
            wanted[k] = TOOLS[k]

    if any(w in lower for w in ("display", "screen", "resolution", "refresh", "hz", "brightness", "scale", "scaling", "rotate", "orientation", "hdr", "night light")):
        add("display.")

    if any(w in lower for w in ("wifi", "network", "internet", "airplane")):
        add("network.")
        if "settings.open" in TOOLS:
            wanted["settings.open"] = TOOLS["settings.open"]

    if any(w in lower for w in ("bluetooth", "bt", "airpods", "headphones")):
        add("bluetooth.")
        if "settings.open" in TOOLS:
            wanted["settings.open"] = TOOLS["settings.open"]

    if any(w in lower for w in ("volume", "audio", "sound", "mute", "unmute")):
        add("audio.")
        if "settings.open" in TOOLS:
            wanted["settings.open"] = TOOLS["settings.open"]

    if any(w in lower for w in ("open", "close", "app")):
        add("apps.")

    # Power
    if any(w in lower for w in ("power", "battery", "hibernate", "timeout", "energy saver", "battery saver", "srum")):
        add("power.")

    # Storage
    if any(w in lower for w in ("storage", "disk", "drive", "cleanup", "recycle bin", "downloads", "temp")):
        add("storage.")

    if any(w in lower for w in ("troubleshoot", "troubleshooter", "diagnostic", "fix issues")):
        add("troubleshoot.")


    # If we still ended up with too many, keep it small:
    # (display tools can be many; but still manageable)
    return wanted


def _route_with_llm(user_text: str) -> Optional[Dict[str, Any]]:
    if not LLM_ROUTER_ENABLED:
        return None

    lower = user_text.strip().lower()
    subset = _tools_for_message(lower)
    if not subset:
        return None

    tool_lines = []
    for t in subset.values():
        tool_lines.append(f"- {t.name}: {t.description}")
    tool_catalog = "\n".join(tool_lines)

    prompt = [
        "You are Jarvis. Choose exactly ONE tool from the list.",
        "Return JSON ONLY. No explanation.",
        "Schema: {\"tool\": string|null, \"params\": object}.",
        "If no tool fits: {\"tool\": null}.",
        "Tools:",
        tool_catalog,
        f"User: {user_text}",
        "JSON:",
    ]

    def _call_model():
        return _coder_model.chat(prompt).strip()

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            fut = ex.submit(_call_model)
            raw = fut.result(timeout=LLM_ROUTER_TIMEOUT_SECONDS)
    except concurrent.futures.TimeoutError:
        # Never hang the CLI. Just skip tool routing.
        return None
    except Exception:
        return None

    obj = _extract_first_json_object(raw)
    if not obj:
        return None

    tool = obj.get("tool")
    params = obj.get("params") or {}

    if not tool or tool not in TOOLS:
        return None
    if not isinstance(params, dict):
        params = {}

    return {"tool": tool, "params": params}


def _detect_apply_to(text_lower: str) -> str:
    # AC = plugged in, DC = on battery
    if any(k in text_lower for k in ["plugged", "plugged in", "ac "]):
        return "ac"
    if any(k in text_lower for k in ["on battery", "battery", "dc "]):
        return "dc"
    return "both"


def _parse_minutes(text_lower: str) -> int | None:
    # supports: "5 minutes", "1 minute", "2 hours", "1 hour"
    m = re.search(r"(\d+)\s*(minute|minutes|hour|hours)", text_lower)
    if not m:
        return None
    n = int(m.group(1))
    unit = m.group(2)
    return n * 60 if "hour" in unit else n


def _apply_to_from_text(t: str) -> str:
    t = t.lower()
    if "on battery" in t or "battery" in t or "dc" in t:
        return "dc"
    if "plugged" in t or "plugged in" in t or "charger" in t or "ac" in t:
        return "ac"
    return "both"


def _extract_int(t: str) -> int | None:
    m = re.search(r"(\d+)", t)
    return int(m.group(1)) if m else None


def handle_user_message(user_message: str) -> None:
    global _PENDING_SUGGESTION

    raw = user_message or ""
    if not raw.strip():
        print("Jarvis: I didn't receive any input.")
        return

    text_lower = raw.strip().lower()
    normalized = _resolve_command(raw)


    # -------------------------
    # HELP
    # -------------------------
    if normalized in ("help", "commands", "what can you do", "what can you do?"):
        print("Jarvis: Here’s what I can do right now:")
        print("  • System        → system info | storage | installed apps")
        print("  • Apps          → open <app> | close <app>")
        print("  • Settings      → open settings <topic> | settings <topic>")
        print("  • Network       → network status | scan wifi | wifi on | wifi off | airplane mode")
        print("  • Display       → display state | brightness <0-100> | brightness up/down")
        print("                 → list displays | resolution 1920x1080 | refresh rate 60")
        print("                 → rotate portrait | orientation landscape | scale 125")
        print("                 → extend/duplicate displays (CONFIRM required)")
        print("  • Runner        → runner is elevated | elevate runner")
        print("  • Bluetooth     → bluetooth status | bluetooth on/off | list paired devices")
        print("  • Audio         → audio status | volume <0-100> | volume up/down | mute/unmute")
        print("  • Reminders     → remind me to <x> at <time> | list reminders | delete reminder <n>")
        print("  • Activity log  → show activity | show activity last <N>")
        return

    # -------------------------
    # MK2 quick commands
    # -------------------------
    if normalized in ("system info", "my system", "pc info"):
        _run_tool("system.get_info", {})
        return
    
    if normalized in ("about", "about status", "pc about", "device about", "device specs"):
        _run_tool("about.get_state", {})
        return

    m = re.search(r"(?:rename|set)\s+(?:this\s+)?(?:pc|computer|device)\s*(?:name)?\s*(?:to)?\s+(.+)$", text_lower)
    if m:
        new_name = m.group(1).strip()
        _run_tool("about.rename_pc", {"name": new_name})
        return


    if normalized in ("storage", "disk space", "drive space"):
        _run_tool("system.get_storage", {})
        return
    
    # Storage: categories
    if normalized in ("storage categories", "storage breakdown", "storage usage"):
        _run_tool("storage.get_categories", {})
        return

    if normalized in ("storage categories deep", "storage breakdown deep", "storage usage deep"):
        _run_tool("storage.get_categories", {"deadline_seconds": 20.0, "max_entries": 300000, "max_depth": 12})
        return

    # Storage: cleanup recommendations
    if normalized in ("cleanup recommendations", "storage cleanup", "cleanup storage"):
        _run_tool("storage.cleanup_recommendations", {})
        return

    if normalized in ("cleanup recommendations deep", "storage cleanup deep", "cleanup storage deep"):
        _run_tool("storage.cleanup_recommendations", {"deadline_seconds": 15.0})
        return



    if normalized in ("list installed apps", "installed apps", "apps list"):
        _run_tool("apps.list_installed", {})
        return

    # Settings
    if text_lower.startswith("open settings "):
        target = raw.strip()[len("open settings "):].strip()
        _run_tool("settings.open", {"target": target or "system"})
        return

    if normalized.startswith("settings "):
        target = raw.strip()[len("settings "):].strip()
        _run_tool("settings.open", {"target": target or "system"})
        return
    
    
    # -------------------------
    # System > Advanced (MK2)
    # -------------------------
    if normalized in ("advanced status", "system advanced status", "advanced settings status"):
        _run_tool("advanced.get_state", {})
        return

    if normalized in ("end task on", "end task off"):
        _run_tool("advanced.set_end_task_in_taskbar", {"enabled": normalized.endswith("on")})
        return

    if normalized in ("file extensions on", "file extensions off",
                      "show file extensions on", "show file extensions off"):
        _run_tool("advanced.set_show_file_extensions", {"enabled": normalized.endswith("on")})
        return


    if normalized in ("hidden files on", "hidden files off", "show hidden files on", "show hidden files off",
                      "hidden & system files on", "hidden & system files off", "show hidden & system files on", "show hidden & system files off"):
        _run_tool("advanced.set_show_hidden_and_system_files", {"enabled": "on" in normalized})
        return

    if normalized in ("full path on", "full path off", "show full path on", "show full path off"):
        _run_tool("advanced.set_show_full_path_in_title_bar", {"enabled": "on" in normalized})
        return

    if normalized in ("empty drives on", "empty drives off", "show empty drives on", "show empty drives off"):
        _run_tool("advanced.set_show_empty_drives", {"enabled": "on" in normalized})
        return

    if normalized in ("run as different user on", "run as different user off",
                      "show run as different user on", "show run as different user off"):
        _run_tool("advanced.set_show_run_as_different_user_in_start", {"enabled": "on" in normalized})
        return

    if normalized in ("troubleshoot list", "list troubleshooters", "troubleshooters"):
        _run_tool("troubleshoot.list", {})
        return

    m = re.search(r"(?:run|start)\s+(.+?)\s+troubleshooter", text_lower)
    if m:
        name = m.group(1).strip()
        _run_tool("troubleshoot.run", {"name": name})
        return

    if normalized in ("open troubleshoot", "open troubleshoot settings", "troubleshoot settings"):
        _run_tool("troubleshoot.open_settings", {})
        return

    # Run by ID:
    # "troubleshoot run id AudioPlaybackDiagnostic"
    if text_lower.startswith("troubleshoot run id "):
        tid = raw.strip()[len("troubleshoot run id "):].strip()
        if not tid:
            print("Jarvis: Please provide an ID, e.g. 'troubleshoot run id AudioPlaybackDiagnostic'.")
            return
        _run_tool("troubleshoot.run", {"id": tid})
        return



    # Power
    if normalized in ("power status", "battery status", "power state", "power plan"):
        _run_tool("power.get_state", {})
        return

    if normalized in ("list power plans", "list power schemes", "power plans"):
        _run_tool("power.list_schemes", {})
        return

    m = re.search(r"(?:set\s+)?power\s+plan(?:\s+to)?\s+(.+)$", text_lower)
    if m:
        plan = m.group(1).strip()
        _run_tool("power.set_scheme", {"name": plan})
        return

    if normalized in ("power mode", "power mode status", "power mode state"):
        _run_tool("power.get_mode", {})
        return

    m = re.search(r"(?:set\s+)?power\s+mode(?:\s+to)?\s+(.+)$", text_lower)
    if m:
        mode = m.group(1).strip()
        _run_tool("power.set_mode", {"mode": mode, "apply_to": "both"})
        return

    # -------------------------
    # Power: timeouts + hibernate + energy saver + battery usage
    # -------------------------

    # Hour-safe handling (prevents the generic minutes-regex below from mis-reading "1 hour" as "1")
    # e.g. "set sleep timeout to 1 hour on battery"
    if "timeout" in text_lower and ("hour" in text_lower or "hours" in text_lower) and "set" in text_lower:
        m = re.search(r"set\s+(screen|sleep|hibernate)\s+timeout\s+to\s+(\d+)\s*(hour|hours)", text_lower)
        if m:
            kind = m.group(1)
            hours = int(m.group(2))
            apply_to = _apply_to_from_text(text_lower)
            tool_map = {
                "screen": "power.set_screen_timeout",
                "sleep": "power.set_sleep_timeout",
                "hibernate": "power.set_hibernate_timeout",
            }
            _run_tool(tool_map[kind], {"minutes": hours * 60, "apply_to": apply_to})
            return

    # -------------------------
    # Power: timeouts / hibernate / energy saver / battery
    # -------------------------

    if normalized in ("power get timeouts", "power timeouts"):
        _run_tool("power.get_timeouts", {})
        return

    # Set timeouts:
    # "set screen timeout to 10 minutes on battery"
    # "set sleep timeout to 15 minutes plugged in"
    # "set hibernate timeout to 60 minutes"
    m = re.search(r"set\s+(screen|sleep|hibernate)\s+timeout\s+to\s+(\d+)\s*(?:minutes|mins|min)?", text_lower)
    if m:
        kind = m.group(1)
        minutes = int(m.group(2))
        apply_to = _apply_to_from_text(text_lower)

        tool_map = {
            "screen": "power.set_screen_timeout",
            "sleep": "power.set_sleep_timeout",
            "hibernate": "power.set_hibernate_timeout",
        }
        _run_tool(tool_map[kind], {"minutes": minutes, "apply_to": apply_to})
        return

    # Hibernate
    if normalized in ("hibernate status", "power hibernate status"):
        _run_tool("power.hibernate_status", {})
        return

    if normalized in ("hibernate on", "enable hibernate"):
        _run_tool("power.hibernate_on", {})
        return

    if normalized in ("hibernate off", "disable hibernate"):
        _run_tool("power.hibernate_off", {})
        return

    # Energy saver
    if normalized in ("energy saver status", "battery saver status"):
        _run_tool("power.energy_saver_status", {})
        return

    if normalized in ("energy saver on", "battery saver on"):
        _run_tool("power.energy_saver_on", {"apply_to": _apply_to_from_text(text_lower)})
        return

    if normalized in ("energy saver off", "battery saver off"):
        _run_tool("power.energy_saver_off", {"apply_to": _apply_to_from_text(text_lower)})
        return

    # "set energy saver threshold to 30"
    if "energy saver threshold" in text_lower or "battery saver threshold" in text_lower:
        percent = _extract_int(text_lower)
        if percent is None:
            print("Jarvis: Please include a percent, e.g. 'set energy saver threshold to 30'.")
            return
        _run_tool("power.energy_saver_threshold", {"percent": percent, "apply_to": _apply_to_from_text(text_lower)})
        return

    # Battery usage settings page
    if normalized in ("battery usage", "open battery usage"):
        _run_tool("power.open_battery_usage", {})
        return

    # Battery report:
    # "battery report" (defaults)
    # "battery report 7 days"
    if text_lower.startswith("battery report"):
        days = _extract_int(text_lower) or 7
        _run_tool("power.battery_report", {"days": days})
        return

    # Per-app usage (SRUM) report
    # "srum report" | "srum report csv" | "srum report xml"
    if text_lower.startswith("srum report"):
        fmt = "csv"
        if "xml" in text_lower:
            fmt = "xml"
        _run_tool("power.srum_report", {"format": fmt})
        return

    # (existing power timeout block kept below, unchanged)

    if normalized in ("power timeouts", "timeouts", "sleep settings", "power timeout status"):
        _run_tool("power.get_timeouts", {})
        return

    # Set screen timeout
    if "screen timeout" in text_lower and ("set" in text_lower or "change" in text_lower):
        mins = _parse_minutes(text_lower)
        if mins is None:
            print("Jarvis: Please specify a time, e.g. 'set screen timeout to 5 minutes'.")
            return
        _run_tool("power.set_screen_timeout", {"minutes": mins, "apply_to": _detect_apply_to(text_lower)})
        return

    # Set sleep timeout
    if "sleep timeout" in text_lower and ("set" in text_lower or "change" in text_lower):
        mins = _parse_minutes(text_lower)
        if mins is None:
            print("Jarvis: Please specify a time, e.g. 'set sleep timeout to 10 minutes'.")
            return
        _run_tool("power.set_sleep_timeout", {"minutes": mins, "apply_to": _detect_apply_to(text_lower)})
        return

    # Set hibernate timeout
    if "hibernate timeout" in text_lower and ("set" in text_lower or "change" in text_lower):
        mins = _parse_minutes(text_lower)
        if mins is None:
            print("Jarvis: Please specify a time, e.g. 'set hibernate timeout to 60 minutes'.")
            return
        _run_tool("power.set_hibernate_timeout", {"minutes": mins, "apply_to": _detect_apply_to(text_lower)})
        return

    # Hibernate on/off
    if normalized in ("hibernate status",):
        _run_tool("power.hibernate_status", {})
        return

    if normalized in ("hibernate on", "turn on hibernate", "enable hibernate"):
        _run_tool("power.hibernate_on", {})
        return

    if normalized in ("hibernate off", "turn off hibernate", "disable hibernate"):
        _run_tool("power.hibernate_off", {})
        return

    # Energy saver
    if normalized in ("energy saver status", "battery saver status"):
        _run_tool("power.energy_saver_status", {})
        return

    if normalized in ("energy saver on", "battery saver on", "turn on energy saver"):
        _run_tool("power.energy_saver_on", {"apply_to": "both"})
        return

    if normalized in ("energy saver off", "battery saver off", "turn off energy saver"):
        _run_tool("power.energy_saver_off", {"apply_to": "both"})
        return

    m = re.search(r"(?:set\s+)?(?:energy\s+saver|battery\s+saver)\s+threshold\s+to\s+(\d{1,3})", text_lower)
    if m:
        pct = max(0, min(100, int(m.group(1))))
        _run_tool("power.energy_saver_threshold", {"percent": pct, "apply_to": _detect_apply_to(text_lower)})
        return

    # Battery usage + report
    if normalized in ("battery usage", "battery usage per app", "battery usage status"):
        _run_tool("power.open_battery_usage", {})
        return

    if normalized.startswith("battery report"):
        # e.g. "battery report 7 days"
        days = 7
        m = re.search(r"(\d+)\s*day", text_lower)
        if m:
            days = int(m.group(1))
        _run_tool("power.battery_report", {"days": days})
        return

    # Network
    if normalized in ("network status", "network state", "wifi status", "wifi state"):
        _run_tool("network.get_state", {})
        return

    if normalized in ("wifi on", "turn wifi on", "enable wifi"):
        _run_tool("network.toggle_wifi", {"enabled": True})
        return

    if normalized in ("wifi off", "turn wifi off", "disable wifi"):
        _run_tool("network.toggle_wifi", {"enabled": False})
        return

    if normalized in ("airplane mode", "open airplane mode"):
        _run_tool("settings.open", {"target": "airplane mode"})
        return

    if normalized in ("airplane mode on", "turn airplane mode on"):
        _run_tool("network.toggle_airplane_mode", {"enabled": True})
        _run_tool("settings.open", {"target": "airplane mode"})
        return

    if normalized in ("airplane mode off", "turn airplane mode off"):
        _run_tool("network.toggle_airplane_mode", {"enabled": False})
        _run_tool("settings.open", {"target": "airplane mode"})
        return
    
    if normalized in ("scan wifi", "list wifi networks", "wifi networks", "nearby wifi"):
        _run_tool("network.list_wifi_networks", {"include_bssids": False, "max_networks": 30})
        return

    if normalized in ("scan wifi detailed",):
        _run_tool("network.list_wifi_networks", {"include_bssids": True, "max_networks": 50})
        return

    if normalized in ("scan wifi", "wifi networks", "list wifi", "list wifi networks", "nearby wifi"):
        _run_tool("network.list_wifi_networks", {"include_bssids": False, "max_networks": 30})
        return

    if normalized in ("scan wifi detailed", "wifi scan detailed"):
        _run_tool("network.list_wifi_networks", {"include_bssids": True, "max_networks": 50})
        return

    if normalized in ("data usage", "network usage"):
        _run_tool("network.get_data_usage_total", {"include_down_adapters": False})
        return

    if normalized in ("wifi usage",):
        _run_tool("network.get_data_usage_current_wifi", {})
        return

    if normalized in ("connection properties", "network properties", "network details"):
        _run_tool("network.get_connection_properties", {})
        return

    if normalized in ("hotspot status", "mobile hotspot status"):
        _run_tool("network.hotspot_status", {})
        return

    if normalized in ("hotspot on", "mobile hotspot on"):
        _run_tool("network.hotspot_toggle", {"enabled": True})
        return

    if normalized in ("hotspot off", "mobile hotspot off"):
        _run_tool("network.hotspot_toggle", {"enabled": False})
        return


    def _run_tool_elevate_if_needed(tool_name: str, params: dict):
        res = _run_tool(tool_name, params)

        payload = None
        if isinstance(res, dict):
            if isinstance(res.get("result"), dict):
                payload = res["result"]
            else:
                payload = res

        if isinstance(payload, dict) and payload.get("needs_elevation"):
            # Ask runner to relaunch elevated (this should trigger UAC)
            _run_tool("runner.relaunch_elevated", {})
            return
        return


    # -------------------------
    # Time & language -> Date & time
    # -------------------------

    # Manual elevate command (always available)
    if normalized in ("elevate runner", "run as admin", "elevate"):
        _run_tool("runner.relaunch_elevated", {})
        return

    if normalized in ("date time status",):
        _run_tool("time.get_state", {})
        return

    # Admin-required (auto-elevate when needed)
    if normalized in ("sync time",):
        _run_tool_elevate_if_needed("time.sync_now", {})
        return

    if normalized in ("auto time on",):
        _run_tool_elevate_if_needed("time.set_auto_time", {"enabled": True})
        return

    if normalized in ("auto time off",):
        _run_tool_elevate_if_needed("time.set_auto_time", {"enabled": False})
        return

    if normalized in ("auto timezone on",):
        _run_tool_elevate_if_needed("time.set_auto_timezone", {"enabled": True})
        return

    if normalized in ("auto timezone off",):
        _run_tool_elevate_if_needed("time.set_auto_timezone", {"enabled": False})
        return

    # Non-admin HKCU toggles (no elevation needed)
    if normalized in ("systray time on",):
        _run_tool("time.set_show_systray_datetime", {"enabled": True})
        return

    if normalized in ("systray time off",):
        _run_tool("time.set_show_systray_datetime", {"enabled": False})
        return

    if normalized in ("notification time on",):
        _run_tool("time.set_show_clock_notification_center", {"enabled": True})
        return

    if normalized in ("notification time off",):
        _run_tool("time.set_show_clock_notification_center", {"enabled": False})
        return

    # e.g. "set time zone to South Africa Standard Time"
    m = re.search(r"^(?:set|change)\s+time\s*zone\s+to\s+(.+)$", raw, flags=re.I)
    if m:
        tz_id = m.group(1).strip().strip('"')
        if tz_id:
            # tzutil may or may not require elevation depending on policy; keep it best-effort
            _run_tool("time.set_timezone", {"timezone_id": tz_id})
            return


    # -------------------------
    # Gaming -> Game Mode
    # -------------------------
    if normalized in ("game mode status",):
        _run_tool("gaming.get_game_mode", {})
        return

    if normalized in ("game mode on",):
        _run_tool("gaming.set_game_mode", {"enabled": True})
        return

    if normalized in ("game mode off",):
        _run_tool("gaming.set_game_mode", {"enabled": False})
        return

    # -------------------------
    # Nearby sharing (MK2)
    # -------------------------
    if normalized in ("nearby sharing status", "nearby sharing state"):
        _run_tool("nearby.get_state", {})
        return

    if normalized in ("nearby sharing off", "turn off nearby sharing"):
        _run_tool("nearby.set_mode", {"mode": "off"})
        return

    if normalized in ("nearby sharing my devices only", "nearby sharing my devices", "my devices only"):
        _run_tool("nearby.set_mode", {"mode": "my_devices_only"})
        return

    if normalized in ("nearby sharing everyone nearby", "nearby sharing everyone", "everyone nearby"):
        _run_tool("nearby.set_mode", {"mode": "everyone_nearby"})
        return

    # rename:
    # "rename nearby sharing to Cade"
    # "set nearby sharing name to Cade"
    m = re.search(r"(?:rename|set)\s+nearby\s+sharing(?:\s+(?:name|device name|friendly name|discoverable name))?\s+to\s+(.+)$", raw, flags=re.I)
    if m:
        new_name = m.group(1).strip().strip('"')
        if not new_name:
            print("Jarvis: Please provide a name, e.g. 'rename nearby sharing to Cade'.")
            return
        _run_tool("nearby.set_friendly_name", {"name": new_name})
        return


    # -------------------------
    # Multitasking
    # -------------------------
    if normalized in ("multitasking status", "multitasking state"):
        _run_tool("multitasking.get_state", {})
        return

    if normalized in ("snap windows on", "enable snap windows"):
        _run_tool("multitasking.set_snap_windows", {"enabled": True})
        return

    if normalized in ("snap windows off", "disable snap windows"):
        _run_tool("multitasking.set_snap_windows", {"enabled": False})
        return

    if normalized in ("title bar shake on", "enable title bar shake", "enable window shake"):
        _run_tool("multitasking.set_title_bar_shake", {"enabled": True})
        return

    if normalized in ("title bar shake off", "disable title bar shake", "disable window shake"):
        _run_tool("multitasking.set_title_bar_shake", {"enabled": False})
        return

    # "alt tab tabs 5" | "alt tab tabs off"
    if text_lower.startswith("alt tab tabs"):
        if "off" in text_lower or "dont" in text_lower:
            _run_tool("multitasking.set_alt_tab_tabs", {"tabs": "dont_show"})
            return
        n = _extract_int(text_lower)
        if n in (3, 5, 20):
            _run_tool("multitasking.set_alt_tab_tabs", {"tabs": str(n)})
            return
        print("Jarvis: Use 'alt tab tabs 3', 'alt tab tabs 5', 'alt tab tabs 20', or 'alt tab tabs off'.")
        return



    # Runner elevation
    if normalized in ("runner is elevated", "runner elevated?", "runner admin", "runner status"):
        _run_tool("runner.is_elevated", {})
        return

    if normalized in ("elevate runner", "runner elevate", "runner restart admin"):
        _run_tool("runner.relaunch_elevated", {})
        return

    if normalized in (
        "restart runner",
        "reset runner",
        "runner restart",
        "runner reset",
        "reset elevation",
        "drop elevation",
    ):
        _run_tool("runner.restart", {})
        return


    # -------------------------
    # Display
    # -------------------------
    if normalized in ("display state", "display status", "brightness status"):
        _run_tool("display.get_state", {})
        return

    match = re.search(r"(?:set\s+brightness|brightness)\s+(\d{1,3})", text_lower)
    if match:
        _run_tool("display.set_brightness", {"level": int(match.group(1))})
        return

    if normalized in ("brightness up", "increase brightness"):
        state = TOOLS["display.get_state"].func({})
        cur = (state.get("result") or {}).get("brightness")
        if cur is None:
            _run_tool("settings.open", {"target": "display"})
            return
        _run_tool("display.set_brightness", {"level": min(100, int(cur) + 10)})
        return

    if normalized in ("brightness down", "decrease brightness"):
        state = TOOLS["display.get_state"].func({})
        cur = (state.get("result") or {}).get("brightness")
        if cur is None:
            _run_tool("settings.open", {"target": "display"})
            return
        _run_tool("display.set_brightness", {"level": max(0, int(cur) - 10)})
        return

    # Display MK2 additions
    if normalized in ("list displays", "display list", "list my displays"):
        _run_tool("display.list_displays", {})
        return

    # "resolution 1920x1080" / "set resolution to 1920 by 1080"
    m = re.search(r"(?:set\s+)?resolution(?:\s+to)?\s+(\d{3,4})\s*(?:x|×|by)\s*(\d{3,4})", text_lower)
    if m:
        _run_tool("display.set_resolution", {"width": int(m.group(1)), "height": int(m.group(2))})
        return

    # "refresh rate 60" / "set refresh rate to 120"
    m = re.search(r"(?:set\s+)?(?:refresh\s*rate|hz)(?:\s+to)?\s+(\d{2,3})", text_lower)
    if m:
        _run_tool("display.set_refresh_rate", {"hz": int(m.group(1))})
        return

    # Rotation/orientation: support "rotate my screen to portrait"
    m = re.search(r"(?:set\s+)?orientation\s+(landscape|portrait|landscape_flipped|portrait_flipped)", text_lower)
    if not m:
        m = re.search(r"(?:rotate\s+(?:my\s+)?(?:screen|display)?\s*(?:to\s+)?)\s*(landscape|portrait|landscape_flipped|portrait_flipped)", text_lower)
    if m:
        _run_tool("display.set_orientation", {"orientation": m.group(1)})
        return

    # Scale: "make my screen smaller/bigger" (deterministic so no LLM hang)
    if "screen" in text_lower or "display" in text_lower:
        if "make" in text_lower and ("smaller" in text_lower or "too big" in text_lower or "zoomed in" in text_lower):
            _run_tool("display.set_scale", {"percent": 100})
            return
        if "make" in text_lower and ("bigger" in text_lower or "too small" in text_lower or "zoomed out" in text_lower):
            _run_tool("display.set_scale", {"percent": 125})
            return

    # "scale 125" / "set scaling to 150%"
    m = re.search(r"(?:set\s+)?(?:scale|scaling)(?:\s+to)?\s+(\d{2,3})%?", text_lower)
    if m:
        _run_tool("display.set_scale", {"percent": int(m.group(1))})
        return

    # Multi display mode keywords
    if "extend" in text_lower and any(w in text_lower for w in ("display", "screen", "screens", "monitor")):
        _run_tool("display.set_multiple_displays", {"mode": "extend"})
        return

    if any(w in text_lower for w in ("duplicate", "mirror", "clone")) and any(w in text_lower for w in ("display", "screen", "screens", "monitor")):
        _run_tool("display.set_multiple_displays", {"mode": "duplicate"})
        return

    if ("pc screen only" in text_lower or "internal" in text_lower) and any(w in text_lower for w in ("display", "screen", "screens", "monitor")):
        _run_tool("display.set_multiple_displays", {"mode": "pc_screen_only"})
        return

    if ("second screen only" in text_lower or "external" in text_lower) and any(w in text_lower for w in ("display", "screen", "screens", "monitor")):
        _run_tool("display.set_multiple_displays", {"mode": "second_screen_only"})
        return

    # Open helpers
    if "color profile" in text_lower or "color management" in text_lower:
        _run_tool("display.open_color_profile", {})
        return

    if "night light" in text_lower:
        _run_tool("display.open_night_light", {})
        return

    if "hdr" in text_lower:
        _run_tool("display.open_hdr_settings", {})
        return

    # -------------------------
    # Bluetooth
    # -------------------------
    if normalized in ("bluetooth status", "bluetooth state", "bt status", "bt state"):
        _run_tool("bluetooth.get_state", {})
        return

    if normalized in ("bluetooth on", "turn bluetooth on", "enable bluetooth"):
        _run_tool("bluetooth.toggle", {"enabled": True})
        return

    if normalized in ("bluetooth off", "turn bluetooth off", "disable bluetooth"):
        _run_tool("bluetooth.toggle", {"enabled": False})
        return

    if normalized in ("list paired devices", "bluetooth paired", "list bluetooth"):
        _run_tool("bluetooth.list_paired", {})
        return

    if normalized.startswith("connect bluetooth ") or normalized.startswith("bluetooth connect "):
        name = raw.split(" ", 2)[2].strip()
        if not name:
            print("Jarvis: Please provide the device name, e.g. 'connect bluetooth AirPods'.")
            return
        _run_tool("bluetooth.connect_paired", {"name": name})
        return
    
    if normalized in ("scan bluetooth", "bluetooth scan", "bt scan", "nearby bluetooth", "list nearby bluetooth"):
        _run_tool("bluetooth.scan_nearby", {"duration_seconds": 6, "active_scan": True, "max_devices": 40})
        return


    # -------------------------
    # Audio
    # -------------------------
    if normalized in ("audio status", "audio state", "volume status", "sound status"):
        _run_tool("audio.get_state", {})
        return

    m = re.search(r"(?:set\s+volume|volume)\s+(\d{1,3})", text_lower)
    if m:
        _run_tool("audio.set_volume", {"level": int(m.group(1))})
        return

    if normalized in ("volume up", "increase volume", "sound up"):
        state = TOOLS["audio.get_state"].func({}).get("result", {})
        cur = state.get("volume")
        if cur is None:
            _run_tool("settings.open", {"target": "sound"})
            return
        _run_tool("audio.set_volume", {"level": min(100, int(cur) + 10)})
        return

    if normalized in ("volume down", "decrease volume", "sound down"):
        state = TOOLS["audio.get_state"].func({}).get("result", {})
        cur = state.get("volume")
        if cur is None:
            _run_tool("settings.open", {"target": "sound"})
            return
        _run_tool("audio.set_volume", {"level": max(0, int(cur) - 10)})
        return

    if normalized in ("mute", "sound mute", "audio mute"):
        _run_tool("audio.set_mute", {"muted": True})
        return

    if normalized in ("unmute", "sound unmute", "audio unmute"):
        _run_tool("audio.set_mute", {"muted": False})
        return
    

    # -------------------------
    # Accessibility -> Vision (Phase 1)
    # -------------------------
    if normalized in ("accessibility vision status",):
        _run_tool("accessibility.get_vision_state", {})
        return

    if normalized in ("transparency effects on",):
        _run_tool("accessibility.set_transparency_effects", {"enabled": True})
        return
    if normalized in ("transparency effects off",):
        _run_tool("accessibility.set_transparency_effects", {"enabled": False})
        return

    if normalized in ("animation effects on",):
        _run_tool("accessibility.set_animation_effects", {"enabled": True})
        return
    if normalized in ("animation effects off",):
        _run_tool("accessibility.set_animation_effects", {"enabled": False})
        return

    if normalized in ("always show scrollbars on",):
        _run_tool("accessibility.set_always_show_scrollbars", {"enabled": True})
        return
    if normalized in ("always show scrollbars off",):
        _run_tool("accessibility.set_always_show_scrollbars", {"enabled": False})
        return

    # e.g. "set text size to 120"
    m = re.search(r"^(?:set\s+)?text\s+size\s+(?:to\s+)?(\d{2,3})%?$", raw, flags=re.I)
    if m:
        _run_tool("accessibility.set_text_size", {"percent": int(m.group(1))})
        return


    # e.g. "dismiss notifications after 30 seconds"
    # supports: 5,7,15,30 seconds, 1 minute, 5 minutes
    m = re.search(r"^dismiss\s+notifications\s+after\s+(\d+)\s*(second|seconds|minute|minutes)$", raw, flags=re.I)
    if m:
        n = int(m.group(1))
        unit = m.group(2).lower()
        seconds = n * 60 if "minute" in unit else n
        _run_tool("accessibility.set_dismiss_notifications_after", {"seconds": seconds})
        return
    
    # -------------------------
    # Accessibility -> Mouse pointer & touch
    # -------------------------
    if normalized in ("mouse pointer status", "mouse pointer and touch status", "accessibility mouse status"):
        _run_tool("accessibility.get_mouse_touch_state", {})
        return

    # Pointer style
    # Examples:
    # "mouse pointer style white"
    # "mouse pointer style custom purple"
    m = re.search(r"^mouse\s+pointer\s+style\s+(white|black|inverted|custom)(?:\s+(.+))?$", raw, flags=re.I)
    if m:
        style = m.group(1).strip().lower()
        color = (m.group(2).strip().lower() if m.group(2) else None)
        payload = {"style": style}
        if color:
            payload["color"] = color
        _run_tool("accessibility.set_mouse_pointer_style", payload)
        return

    # Shortcut: "mouse pointer color purple" (forces custom)
    m = re.search(r"^mouse\s+pointer\s+color\s+(.+)$", raw, flags=re.I)
    if m:
        color = m.group(1).strip().lower()
        _run_tool("accessibility.set_mouse_pointer_style", {"style": "custom", "color": color})
        return

    # Pointer size
    # "set mouse pointer size to 5"
    m = re.search(r"^(?:set\s+)?mouse\s+pointer\s+size\s+(?:to\s+)?(\d+)$", raw, flags=re.I)
    if m:
        _run_tool("accessibility.set_mouse_pointer_size", {"size": int(m.group(1))})
        return

    # Mouse indicator (Ctrl highlight)
    if normalized in ("mouse indicator on",):
        _run_tool("accessibility.set_mouse_indicator", {"enabled": True})
        return
    if normalized in ("mouse indicator off",):
        _run_tool("accessibility.set_mouse_indicator", {"enabled": False})
        return

    # Pointer trails
    if normalized in ("pointer trails off", "mouse trails off"):
        _run_tool("accessibility.set_mouse_pointer_trails", {"enabled": False})
        return
    if normalized in ("pointer trails on", "mouse trails on"):
        _run_tool("accessibility.set_mouse_pointer_trails", {"enabled": True})
        return

    # "pointer trails length 12"
    m = re.search(r"^pointer\s+trails\s+length\s+(\d+)$", raw, flags=re.I)
    if m:
        _run_tool("accessibility.set_mouse_pointer_trails_length", {"length": int(m.group(1))})
        return

    # Pointer shadow
    if normalized in ("pointer shadow on",):
        _run_tool("accessibility.set_mouse_pointer_shadow", {"enabled": True})
        return
    if normalized in ("pointer shadow off",):
        _run_tool("accessibility.set_mouse_pointer_shadow", {"enabled": False})
        return


    # Touch indicator
    if normalized in ("touch indicator on",):
        _run_tool("accessibility.set_touch_indicator", {"enabled": True})
        return
    if normalized in ("touch indicator off",):
        _run_tool("accessibility.set_touch_indicator", {"enabled": False})
        return

    # "touch indicator darker on" / "touch indicator darker off" (enhanced mode)
    if normalized in ("touch indicator darker on", "make touch circle darker on"):
        # Implicitly enable touch indicator first, then enable enhanced mode.
        _run_tool("accessibility.set_touch_indicator", {"enabled": True})
        _run_tool("accessibility.set_touch_indicator_enhanced", {"enabled": True})
        return
    if normalized in ("touch indicator darker off", "make touch circle darker off"):
        _run_tool("accessibility.set_touch_indicator_enhanced", {"enabled": False})
        return


    # ---- MK3.2: deterministic routing for logs + code introspection ----
    # Put this BEFORE the final "chat" fallback.

    # 1) Logs: list recent logs
    if normalized in ("list logs", "list recent logs", "recent logs", "show logs", "logs"):
        return _run_tool("logs.list", {"limit": 10})

    # 2) Logs: show last N lines of the current log
    m = re.match(r"^show last (\d+)\s+lines of the current log$", normalized)
    if m:
        return _run_tool("logs.last", {"lines": int(m.group(1))})

    m = re.match(r"^show last (\d+)\s+lines of (the )?current session log$", normalized)
    if m:
        return _run_tool("logs.last", {"lines": int(m.group(1))})

    m = re.match(r"^tail (the )?current log( (\d+))?$", normalized)
    if m:
        lines = int(m.group(3) or 50)
        return _run_tool("logs.last", {"lines": lines})

    # 3) Logs: summarize last N lines of the current log
    m = re.match(r"^summari[sz]e the last (\d+)\s+lines of the current log$", normalized)
    if m:
        return _run_tool("logs.summarize_tail", {"lines": int(m.group(1))})

    m = re.match(r"^summari[sz]e (the )?current log( (\d+))?$", normalized)
    if m:
        lines = int(m.group(3) or 80)
        return _run_tool("logs.summarize_tail", {"lines": lines})

    # 4) Logs: tail or summarize a specific log file
    m = re.match(r"^(tail|show)\s+log\s+(.+?)\s+(\d+)$", normalized)
    if m:
        file_ = m.group(2).strip()
        lines = int(m.group(3))
        return _run_tool("logs.tail", {"file": file_, "lines": lines})

    m = re.match(r"^summari[sz]e\s+log\s+(.+?)\s+(\d+)$", normalized)
    if m:
        file_ = m.group(1).strip()
        lines = int(m.group(2))
        return _run_tool("logs.summarize_tail", {"file": file_, "lines": lines})

    # 5) Code: read file phrasing like "read agent/core.py first 80 lines"
    m = re.match(r"^read\s+(.+?)\s+first\s+(\d+)\s+lines$", normalized)
    if m:
        path = m.group(1).strip()
        max_lines = int(m.group(2))
        return _run_tool("code.read_file", {"path": path, "max_lines": max_lines, "start_line": 1})

    # Alternative phrasing: "read file agent/core.py 80"
    m = re.match(r"^(read|open)\s+file\s+(.+?)\s+(\d+)$", normalized)
    if m:
        path = m.group(2).strip()
        max_lines = int(m.group(3))
        return _run_tool("code.read_file", {"path": path, "max_lines": max_lines, "start_line": 1})

    # 6) Code: search phrasing like 'search for "load_model_roles" in agent'
    m = re.match(r'^search for "(.*?)"\s+in\s+(.+)$', normalized)
    if m:
        query = m.group(1).strip()
        path = m.group(2).strip()
        return _run_tool("code.search", {"query": query, "path": path})

    # Alternative: search code for X in Y
    m = re.match(r"^search code for (.+?)\s+in\s+(.+)$", normalized)
    if m:
        query = m.group(1).strip().strip('"')
        path = m.group(2).strip()
        return _run_tool("code.search", {"query": query, "path": path})


    # -------------------------
    # UI Automation (UIA)
    # -------------------------
    if normalized in (
        "uia status",
        "uia get status",
        "uia state",
        "ui automation status",
        "ui automation",
        "uia",
    ):
        _run_tool("uia.get_status", {})
        return



    # -------------------------
    # MK1 commands
    # -------------------------
    if text_lower.startswith("summarise:") or text_lower.startswith("summarize:"):
        parts = raw.split(":", 1)
        if len(parts) < 2 or not parts[1].strip():
            print("Jarvis: You asked me to summarise, but didn't give any text.")
            return

        content = parts[1].strip()
        print("Jarvis: (summarising)...")
        reply = _research_model.chat([
            "You are a helpful, concise assistant.",
            "Summarise this text clearly and briefly:",
            content,
            "Summary:",
        ])
        print(f"Jarvis: {reply}")
        return

    if text_lower.startswith("remind me"):
        when_str = _extract_when_from_text(raw)
        _run_tool("create_reminder", {"text": raw, "when": when_str})
        return

    if text_lower.startswith("open "):
        app_name = raw.strip()[len("open "):].strip()
        if not app_name:
            print("Jarvis: You asked me to open something, but I don't know which app.")
            return
        _run_tool("open_application", {"app_name": app_name})
        return

    if text_lower.startswith("close "):
        app_name = raw.strip()[len("close "):].strip()
        if not app_name:
            print("Jarvis: You asked me to close something, but I don't know which app.")
            return
        _run_tool("close_application", {"app_name": app_name})
        return

    if normalized in ("list reminders", "show reminders", "show my reminders"):
        _run_tool("list_reminders", {})
        return

    if normalized in ("show activity", "show audit log", "show log") or "activity log" in text_lower:
        limit = 10
        m = re.search(r"last\s+(\d+)", text_lower)
        if m:
            try:
                limit = int(m.group(1))
            except ValueError:
                limit = 10
        _run_tool("show_activity", {"limit": limit})
        return

    if text_lower.startswith("delete reminder") or text_lower.startswith("remove reminder"):
        m = re.search(r"(\d+)", text_lower)
        if not m:
            print("Jarvis: Please tell me which reminder number to delete (e.g. 'delete reminder 2').")
            return
        _run_tool("delete_reminder", {"index": int(m.group(1))})
        return

    if normalized in ("clear reminders", "delete all reminders", "remove all reminders"):
        _run_tool("clear_reminders", {})
        return

    # -------------------------
    # "Did you mean...?" (typo help)
    # -------------------------
    if len(normalized.split()) <= 5:
        suggestion = _did_you_mean(normalized)
        if suggestion:
            _PENDING_SUGGESTION = suggestion
            print(f"Jarvis: Did you mean '{suggestion}'? (yes/no)")
            return

    # -------------------------
    # LLM tool-router fallback (safe timeout)
    # -------------------------
    routed = _route_with_llm(raw)
    if routed:
        _run_tool(routed["tool"], routed["params"])
        return

    # -------------------------
    # fallback chat
    # -------------------------
    print("Jarvis: (thinking)...")
    reply = _general_model.chat([
        "You are a helpful, concise assistant named Jarvis.",
        f"User: {user_message}",
        "Assistant:",
    ])
    print(f"Jarvis: {reply}")
