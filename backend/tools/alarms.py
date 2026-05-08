from __future__ import annotations

import plistlib
import re
import subprocess
import uuid as _uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from ._util import local_now
from .registry import REGISTRY, ToolEntry

_DAY_BITS: dict[str, int] = {
    "monday": 1, "mon": 1,
    "tuesday": 2, "tue": 2, "tues": 2,
    "wednesday": 4, "wed": 4,
    "thursday": 8, "thu": 8, "thur": 8, "thurs": 8,
    "friday": 16, "fri": 16,
    "saturday": 32, "sat": 32,
    "sunday": 64, "sun": 64,
}
_DAY_NAMES_PATTERN = (
    r"\b(monday|tuesday|wednesday|thursday|friday|saturday|sunday|"
    r"mon|tue|tues|wed|thu|thur|thurs|fri|sat|sun)\b"
)

_REPEAT_NEVER = 0
_REPEAT_EVERY_DAY = 127
_REPEAT_WEEKDAYS = 31   # Mon–Fri
_REPEAT_WEEKENDS = 96   # Sat+Sun


def _extract_clock_hm(cleaned: str) -> tuple[int, int] | None:
    s = cleaned.strip()
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", s, re.I)
    if m:
        h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    else:
        m = re.search(r"\b(\d{1,2})\s+(\d{1,2})\s*(am|pm)\b", s, re.I)
        if m:
            h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        else:
            m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s, re.I)
            if m:
                h, mn, ap = int(m.group(1)), 0, m.group(2).lower()
            else:
                m = re.search(r"\b([01]?\d|2[0-3]):([0-5]\d)\b", s)
                if not m:
                    return None
                h, mn = int(m.group(1)), int(m.group(2))
                if not (0 <= h <= 23 and 0 <= mn <= 59):
                    return None
                return h, mn
    if not (1 <= h <= 12 and 0 <= mn <= 59):
        return None
    if ap == "pm" and h != 12:
        h += 12
    elif ap == "am" and h == 12:
        h = 0
    return h, mn


def _parse_alarm_time(time_str: str) -> tuple[datetime, int]:
    raw = time_str.strip().lower()
    now = local_now()

    m = re.match(r"in\s+(\d+(?:\.\d+)?)\s*(second|sec|minute|min|hour|hr)s?", raw)
    if m:
        val = float(m.group(1))
        unit = m.group(2)
        if unit in ("second", "sec"):
            delta = timedelta(seconds=val)
        elif unit in ("minute", "min"):
            delta = timedelta(minutes=val)
        else:
            delta = timedelta(hours=val)
        target = now + delta
        return target, int(delta.total_seconds())

    is_tomorrow = "tomorrow" in raw
    cleaned = re.sub(r"tomorrow\s*(at\s*)?", "", raw).strip()
    cleaned = re.sub(_DAY_NAMES_PATTERN, "", cleaned)
    cleaned = re.sub(r"\b(at|on|for|only|each|every)\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    hm = _extract_clock_hm(cleaned)
    if hm is None:
        raise ValueError(f"Cannot parse time expression: {time_str!r}")
    h, mn = hm

    if not is_tomorrow:
        day_tokens = re.findall(_DAY_NAMES_PATTERN, raw)
        uniq_bits = {_DAY_BITS[t] for t in day_tokens if t in _DAY_BITS}
        if len(uniq_bits) == 1:
            bit = next(iter(uniq_bits))
            want_wd = bit.bit_length() - 1
            candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            d = (want_wd - candidate.weekday()) % 7
            if d == 0 and candidate <= now:
                d = 7
            target = candidate + timedelta(days=d)
            return target, max(1, int((target - now).total_seconds()))

    target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
    if is_tomorrow or target <= now:
        target += timedelta(days=1)
    return target, max(1, int((target - now).total_seconds()))


def _distinct_weekday_bits(text: str) -> set[int]:
    return {_DAY_BITS[t] for t in re.findall(_DAY_NAMES_PATTERN, text.lower()) if t in _DAY_BITS}


def _restart_mobiletimerd() -> None:
    pid = subprocess.run(["pgrep", "mobiletimerd"], capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)


def _write_mt_alarm(hour: int, minute: int, repeat: int = _REPEAT_EVERY_DAY) -> bool:
    entry: dict[str, Any] = {
        "$MTAlarm": {
            "MTAlarmAllowsSnooze": True,
            "MTAlarmBedtimeDismissAction": 0,
            "MTAlarmBedtimeDoNotDisturb": False,
            "MTAlarmBedtimeDoNotDisturbOptions": 0,
            "MTAlarmBedtimeHour": 0,
            "MTAlarmBedtimeMinute": 0,
            "MTAlarmDataVersion": 3.0,
            "MTAlarmDismissAction": 0,
            "MTAlarmEnabled": True,
            "MTAlarmHour": hour,
            "MTAlarmID": str(_uuid.uuid4()).upper(),
            "MTAlarmIsSleep": False,
            "MTAlarmLastModifiedDate": datetime.now(timezone.utc).replace(tzinfo=None),
            "MTAlarmMinute": minute,
            "MTAlarmOnboardingVersion": 0,
            "MTAlarmRepeatSchedule": repeat,
            "MTAlarmSleepScheduleKey": False,
            "MTAlarmSound": {
                "$MTSound": {"MTSoundToneID": "system:Radial", "MTSoundType": 2}
            },
            "MTAlarmTimeInBedTrackingKey": False,
        }
    }
    export = subprocess.run(
        ["defaults", "export", "com.apple.mobiletimerd", "-"],
        capture_output=True, timeout=5,
    )
    if export.returncode != 0:
        return False
    data = plistlib.loads(export.stdout)
    container = data.setdefault("MTAlarms", {"MTAlarms": [], "MTSleepAlarms": []})
    container.setdefault("MTAlarms", []).append(entry)
    xml_bytes = plistlib.dumps(data, fmt=plistlib.FMT_XML)
    imp = subprocess.run(
        ["defaults", "import", "com.apple.mobiletimerd", "-"],
        input=xml_bytes, capture_output=True, timeout=5,
    )
    if imp.returncode != 0:
        return False
    _restart_mobiletimerd()
    return True


def _reminder_alarm_fallback(seconds: int, label: str, time_fmt: str) -> str:
    safe_label = label.replace('"', "'").replace("\\", "")
    secs_int = max(int(seconds), 60)
    script = f"""tell application "Reminders"
    set myList to default list
    set r to make new reminder at end of myList
    set name of r to "Alarm: {safe_label}"
    set due date of r to (current date) + {secs_int}
    set remind me date of r to (current date) + {secs_int}
end tell"""
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=10)
    if result.returncode != 0:
        return f"Failed to schedule reminder for {time_fmt}."
    return f"Alarm set for {time_fmt} in Reminders (Clock.app unavailable)."


def _read_mt_alarms() -> list[dict]:
    try:
        export = subprocess.run(
            ["defaults", "export", "com.apple.mobiletimerd", "-"],
            capture_output=True, timeout=5,
        )
        if export.returncode != 0 or not export.stdout:
            return []
        data = plistlib.loads(export.stdout)
        return data.get("MTAlarms", {}).get("MTAlarms", [])
    except Exception:
        return []


def _is_cancel_all_phrase(text: str) -> bool:
    t = text.strip().lower()
    if not t:
        return False
    if re.search(
        r"\b(delete|remove|cancel|clear|dismiss|drop|erase)\s+all(\s+\w+)*\s+alarms?\b", t
    ):
        return True
    if re.search(r"\b(delete|remove|cancel|clear)\s+every\s+alarm\b", t):
        return True
    if t in (
        "all alarms", "delete all alarms", "cancel all alarms",
        "remove all alarms", "clear all alarms",
        "delete all my alarms", "cancel all my alarms",
    ):
        return True
    if ("alarm" in t or "alarms" in t) and "all" in t:
        if any(w in t for w in ("delete", "remove", "cancel", "clear", "dismiss", "drop", "erase")):
            return True
    return False


async def _set_alarm(args: dict[str, Any]) -> str:
    time_str = str(args.get("time", "")).strip()
    label = str(args.get("label") or "JARVIS Alarm").strip() or "JARVIS Alarm"
    if not time_str:
        return "Please specify a time for the alarm."

    try:
        target, secs_from_now = _parse_alarm_time(time_str)
    except ValueError as exc:
        return f"Could not understand the time '{time_str}': {exc}"

    hour, minute = target.hour, target.minute
    time_fmt = target.strftime("%-I:%M %p")
    combined = (time_str + " " + label).lower()

    if any(w in combined for w in ("once", "one time", "one-time", "today only", "just today")):
        repeat, repeat_desc = _REPEAT_NEVER, "once"
    elif any(w in combined for w in ("every day", "everyday", "daily")):
        repeat, repeat_desc = _REPEAT_EVERY_DAY, "every day"
    elif any(w in combined for w in ("weekday", "weekdays", "work day", "monday to friday")):
        repeat, repeat_desc = _REPEAT_WEEKDAYS, "weekdays"
    elif any(w in combined for w in ("weekend", "weekends")):
        repeat, repeat_desc = _REPEAT_WEEKENDS, "weekends"
    else:
        scan_chunks: list[str] = []
        for m in re.finditer(r"\b(every|each)\s+([^.;]{1,200})", combined):
            scan_chunks.append(m.group(2))
        rm = re.search(r"\brepeat(?:s|ing)?\s+(?:on\s+)?([^.;]{1,200})", combined)
        if rm:
            scan_chunks.append(rm.group(1))
        scan_text = " ".join(scan_chunks).lower()
        day_mask = 0
        day_names: list[str] = []
        for token in re.findall(r"\b\w+\b", scan_text):
            if token in _DAY_BITS and not (_DAY_BITS[token] & day_mask):
                day_mask |= _DAY_BITS[token]
                canon = next(k for k, v in _DAY_BITS.items() if v == _DAY_BITS[token] and len(k) > 3)
                day_names.append(canon.capitalize())
        if day_mask:
            repeat, repeat_desc = day_mask, "/".join(day_names)
        else:
            repeat, repeat_desc = _REPEAT_NEVER, "once"

    _once_words = ("once", "one time", "one-time", "today only", "just today")
    implicit_weekday_repeat = False
    if repeat == _REPEAT_NEVER and not any(w in combined for w in _once_words):
        nd = _distinct_weekday_bits(combined)
        if len(nd) == 1:
            bit = next(iter(nd))
            repeat = bit
            repeat_desc = next(k for k, v in _DAY_BITS.items() if v == bit and len(k) > 3).capitalize()
            implicit_weekday_repeat = True

    if _write_mt_alarm(hour, minute, repeat):
        if repeat == _REPEAT_NEVER:
            how = "does not repeat"
        elif implicit_weekday_repeat:
            how = (
                f"repeats every {repeat_desc} "
                "(Clock has no true one-off on a named weekday — it uses a weekly alarm for that day)"
            )
        else:
            how = f"repeats {repeat_desc}"
        return f"Alarm set for {time_fmt} ({how}). It will appear in the Clock app's Alarms tab."

    return _reminder_alarm_fallback(secs_from_now, label, time_fmt)


async def _list_alarms(args: dict[str, Any]) -> str:
    alarms = _read_mt_alarms()
    if not alarms:
        return "No alarms set. Say 'set an alarm for 7 AM' to create one."
    lines = []
    for a in alarms:
        inner = a.get("$MTAlarm", {})
        h = inner.get("MTAlarmHour", 0)
        m = inner.get("MTAlarmMinute", 0)
        enabled = "ON" if inner.get("MTAlarmEnabled", True) else "OFF"
        ampm = "AM" if h < 12 else "PM"
        h12 = h % 12 or 12
        lines.append(f"• {h12}:{m:02d} {ampm} [{enabled}]")
    return "Clock app alarms:\n" + "\n".join(lines)


async def _cancel_alarm(args: dict[str, Any]) -> str:
    if args.get("all_alarms") is True:
        return _cancel_all_alarms()
    q = " ".join(
        str(x).strip()
        for x in (args.get("time"), args.get("label"))
        if x is not None and str(x).strip()
    ).strip()
    query = q or str(args.get("label") or args.get("time") or "")

    if _is_cancel_all_phrase(query):
        return _cancel_all_alarms()
    if not query.strip():
        return "Please specify the alarm time to cancel (e.g. '7 AM' or '1:20 PM Sunday')."

    try:
        target, _ = _parse_alarm_time(query)
    except ValueError:
        return f"Could not understand time '{query}'."
    th, tm = target.hour, target.minute
    day_bits = _distinct_weekday_bits(query.lower())
    repeat_filter: int | None = None
    if day_bits:
        repeat_filter = 0
        for b in day_bits:
            repeat_filter |= b

    def _matches(inner: dict[str, Any]) -> bool:
        if inner.get("MTAlarmHour") != th or inner.get("MTAlarmMinute") != tm:
            return False
        if repeat_filter is None:
            return True
        return int(inner.get("MTAlarmRepeatSchedule") or 0) == repeat_filter

    try:
        export = subprocess.run(
            ["defaults", "export", "com.apple.mobiletimerd", "-"],
            capture_output=True, timeout=5,
        )
        if export.returncode != 0 or not export.stdout:
            return "Could not read alarm database."
        data = plistlib.loads(export.stdout)
        container = data.get("MTAlarms", {})
        alarms: list = container.get("MTAlarms", [])
        before = len(alarms)
        alarms = [a for a in alarms if not _matches(a.get("$MTAlarm", {}))]
        removed = before - len(alarms)
        if removed == 0:
            tf = target.strftime("%-I:%M %p")
            hint = (
                f" If several alarms share that time, include the day (e.g. 'cancel {tf} Sunday')."
                if repeat_filter is None
                else " Check the time and day (repeat schedule must match exactly)."
            )
            return f"No Clock.app alarm found at {tf}.{hint}"
        container["MTAlarms"] = alarms
        data["MTAlarms"] = container
        xml_bytes = plistlib.dumps(data, fmt=plistlib.FMT_XML)
        imp = subprocess.run(
            ["defaults", "import", "com.apple.mobiletimerd", "-"],
            input=xml_bytes, capture_output=True, timeout=5,
        )
        if imp.returncode != 0:
            return f"Failed to save: {imp.stderr.decode().strip()}"
        _restart_mobiletimerd()
        return f"Cancelled alarm at {target.strftime('%-I:%M %p')} from Clock app."
    except Exception as exc:
        return f"Failed to cancel alarm: {exc}"


def _cancel_all_alarms() -> str:
    try:
        export = subprocess.run(
            ["defaults", "export", "com.apple.mobiletimerd", "-"],
            capture_output=True, timeout=5,
        )
        if export.returncode != 0 or not export.stdout:
            return "Could not read alarm database."
        data = plistlib.loads(export.stdout)
        container = data.setdefault("MTAlarms", {"MTAlarms": [], "MTSleepAlarms": []})
        before = len(container.get("MTAlarms", []))
        container["MTAlarms"] = []
        data["MTAlarms"] = container
        xml_bytes = plistlib.dumps(data, fmt=plistlib.FMT_XML)
        imp = subprocess.run(
            ["defaults", "import", "com.apple.mobiletimerd", "-"],
            input=xml_bytes, capture_output=True, timeout=5,
        )
        if imp.returncode != 0:
            return f"Failed to save: {imp.stderr.decode().strip()}"
        _restart_mobiletimerd()
        if before == 0:
            return "No alarms were set in the Clock app."
        return f"Removed all {before} alarm(s) from the Clock app."
    except Exception as exc:
        return f"Failed to clear alarms: {exc}"


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": (
                "Set a macOS Clock alarm (mobiletimerd plist). "
                "Use when the user says 'alarm', 'wake me at X', or a clock time. "
                "Default is one-time. When the user asks for repeating days, include their exact "
                "repeat phrase in the ``time`` argument, not only the clock time."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": (
                            "When to fire — include the SAME natural-language repeat wording the user used "
                            "when they asked for repeating alarms. Examples: '7:30 AM', 'in 2 hours', "
                            "'Wednesday 9 PM', '1 PM every Wednesday'."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": "Short label.",
                        "default": "JARVIS Alarm",
                    },
                },
                "required": ["time"],
            },
        },
    },
    handler=_set_alarm,
    thinking_label="Setting alarm…",
    terminal=True,
    help_hint="Puts an alarm in the Clock app; include repeat phrases in `time` when the user asked for them.",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "list_alarms",
            "description": "List upcoming alarms from the macOS Clock app.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_list_alarms,
    thinking_label="Checking alarms…",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "cancel_alarm",
            "description": (
                "Delete alarm(s) from the macOS Clock app. Match by clock time. "
                "Use all_alarms=true to remove every Clock alarm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {"type": "string", "description": "Alarm time to remove, e.g. '1:20 PM' or '1:20 PM Sunday'."},
                    "label": {"type": "string", "description": "Optional extra words combined with time when cancelling."},
                    "all_alarms": {"type": "boolean", "description": "If true, delete every alarm in the Clock app."},
                },
                "required": [],
            },
        },
    },
    handler=_cancel_alarm,
    thinking_label="Cancelling alarm…",
    terminal=True,
    help_hint="set all_alarms=true to clear every Clock alarm; otherwise merge time+label.",
))
