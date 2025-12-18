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
from .models import ChatModel

_policy = Policy.load()
_chat_model = ChatModel()   # local LLM "brain"

# If Jarvis suggests a command, store it here so "yes" can execute it.
_PENDING_SUGGESTION: Optional[str] = None

# Limit LLM tool-router so it never hangs the CLI
LLM_ROUTER_TIMEOUT_SECONDS = 6
LLM_ROUTER_ENABLED = True


# -------------------------
# Normalization + Aliases
# -------------------------
def _normalize(text: str) -> str:
    text = (text or "").strip().lower()
    text = re.sub(r"[?!.,;:]+$", "", text)
    return " ".join(text.split())


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
}


KNOWN_COMMANDS = set(ALIASES.keys()) | {
    "help", "commands", "what can you do", "what can you do?",
    "system info", "my system", "pc info",
    "storage", "disk space", "drive space",
    "list installed apps", "installed apps", "apps list",

    "network status", "network state", "wifi status", "wifi state",
    "wifi on", "wifi off",
    "airplane mode", "open airplane mode",
    "airplane mode on", "turn airplane mode on",
    "airplane mode off", "turn airplane mode off",

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
}


def _did_you_mean(normalized: str) -> Optional[str]:
    if not normalized or normalized in KNOWN_COMMANDS:
        return None
    matches = difflib.get_close_matches(normalized, list(KNOWN_COMMANDS), n=1, cutoff=0.78)
    return matches[0] if matches else None


def _run_tool(tool_name: str, params: Dict[str, Any]) -> None:
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
                return
        else:
            choice = input("Proceed? (y/n): ").strip().lower()
            if choice not in ("y", "yes"):
                print("Jarvis: Okay, I cancelled that action.")
                log_action(tool, params, "cancelled")
                return

    try:
        result = tool.func(params)
        log_action(tool, params, "success")
        if result is not None:
            print(f"Jarvis: Tool returned: {result}")
    except Exception as exc:
        print(f"Jarvis: Something went wrong while executing the tool: {exc}")
        log_action(tool, params, f"error: {exc}")


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
        return _chat_model.chat(prompt).strip()

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


def handle_user_message(user_message: str) -> None:
    global _PENDING_SUGGESTION

    raw = user_message or ""
    if not raw.strip():
        print("Jarvis: I didn't receive any input.")
        return

    text_lower = raw.strip().lower()
    normalized = _normalize(raw)

    # If Jarvis previously suggested a command, handle yes/no.
    if normalized in ("y", "yes") and _PENDING_SUGGESTION:
        normalized = _PENDING_SUGGESTION
        text_lower = normalized
        raw = normalized
        _PENDING_SUGGESTION = None
    elif normalized in ("n", "no") and _PENDING_SUGGESTION:
        print("Jarvis: Okay, cancelled.")
        _PENDING_SUGGESTION = None
        return

    # Apply alias rewrite
    normalized = ALIASES.get(normalized, normalized)

    # -------------------------
    # HELP
    # -------------------------
    if normalized in ("help", "commands", "what can you do", "what can you do?"):
        print("Jarvis: Here’s what I can do right now:")
        print("  • System        → system info | storage | installed apps")
        print("  • Apps          → open <app> | close <app>")
        print("  • Settings      → open settings <topic> | settings <topic>")
        print("  • Network       → network status | wifi on | wifi off | airplane mode")
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

    if normalized in ("storage", "disk space", "drive space"):
        _run_tool("system.get_storage", {})
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

    # Runner elevation
    if normalized in ("runner is elevated", "runner elevated?", "runner admin", "runner status"):
        _run_tool("runner.is_elevated", {})
        return

    if normalized in ("elevate runner", "runner elevate", "runner restart admin"):
        _run_tool("runner.relaunch_elevated", {})
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
    # MK1 commands
    # -------------------------
    if text_lower.startswith("summarise:") or text_lower.startswith("summarize:"):
        parts = raw.split(":", 1)
        if len(parts) < 2 or not parts[1].strip():
            print("Jarvis: You asked me to summarise, but didn't give any text.")
            return

        content = parts[1].strip()
        print("Jarvis: (summarising)...")
        reply = _chat_model.chat([
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
    reply = _chat_model.chat([
        "You are a helpful, concise assistant named Jarvis.",
        f"User: {user_message}",
        "Assistant:",
    ])
    print(f"Jarvis: {reply}")
