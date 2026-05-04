"""Tool implementations for JARVIS (Phase 5).

OpenAI-style JSON schemas + async runners. Filesystem access is confined to
``JARVIS_FS_ROOT``. Calendar reads a local ``.ics`` file from
``JARVIS_CALENDAR_ICS``. Web search uses Tavily when ``TAVILY_API_KEY`` is set.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import plistlib
import re
import subprocess
import tempfile
import time as _time
import uuid as _uuid
from collections.abc import Callable, Coroutine
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]


def _local_now() -> datetime:
    """Wall-clock 'now' for alarms. Use when writing MTAlarmHour/Minute (local time).

    If the backend process TZ differs from the Mac (e.g. Docker defaults to UTC),
    set ``JARVIS_TIMEZONE`` to an IANA name (e.g. ``America/Los_Angeles``) so
    wall-clock hours match the Clock app.
    """
    tz_name = os.environ.get("JARVIS_TIMEZONE", "").strip()
    if tz_name and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            logger.warning("Invalid JARVIS_TIMEZONE=%r; using system local time.", tz_name)
    return datetime.now().astimezone()

import httpx

logger = logging.getLogger("jarvis.tools")

# Injected by main.py at startup so reminder tool can broadcast alerts.
_broadcast_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None


def configure_tools(broadcast_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]]) -> None:
    global _broadcast_fn
    _broadcast_fn = broadcast_fn

# --- OpenAI-compatible tool definitions (subset used by DeepSeek) ----------

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for current facts, news, or documentation. "
                "Use when the user asks for something that may have changed after "
                "your training cutoff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Concise English search query.",
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": (
                "List files and subdirectories inside the JARVIS workspace sandbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": (
                            "Relative path inside the workspace (use '.' for root)."
                        ),
                        "default": ".",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return (capped at 100).",
                        "default": 40,
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": (
                "Read a UTF-8 text file from the JARVIS workspace sandbox."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path to the file.",
                    },
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read (default 65536).",
                        "default": 65536,
                    },
                },
                "required": ["path"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "calendar_upcoming",
            "description": "List upcoming events from the user's linked calendar (.ics file).",
            "parameters": {
                "type": "object",
                "properties": {
                    "days_ahead": {"type": "integer", "default": 14},
                    "max_events": {"type": "integer", "default": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_reminder",
            "description": (
                "Schedule an in-session reminder (HUD + optional speech) after a delay. "
                "Use when the user says 'remind me in X minutes/hours' with no wall-clock time. "
                "For alarm clock times use set_alarm instead."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "message": {
                        "type": "string",
                        "description": "What to remind them about (the reminder body).",
                    },
                    "minutes": {"type": "number", "description": "Delay in minutes (e.g. 0.5 for 30 seconds)."},
                    "name": {
                        "type": "string",
                        "description": "Optional short title shown with the reminder (e.g. 'Meeting', 'Take meds').",
                    },
                },
                "required": ["minutes"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open a macOS application by name. E.g. Spotify, Chrome, VS Code, Terminal, Finder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application name as it appears in /Applications."},
                },
                "required": ["app_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set macOS system output volume (0–100). Also accepts 'mute' or 'unmute'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "description": "Volume level 0–100."},
                    "mute": {"type": "boolean", "description": "True to mute, false to unmute."},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set macOS display brightness (0–100). Requires: brew install brightness.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "description": "Brightness level 0–100."},
                },
                "required": ["percent"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the current contents of the macOS clipboard.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Write text to the macOS clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy."},
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "take_screenshot",
            "description": "Capture the screen and describe what's on it using vision AI. Use when asked 'what's on my screen'.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the web browser. Use for 'open this site', 'go to', 'navigate to', 'open YouTube', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL including https://. For well-known sites use their canonical URL."},
                    "browser": {"type": "string", "description": "Optional: 'chrome', 'safari', 'firefox', 'arc'. Defaults to system default."},
                },
                "required": ["url"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Open the browser and search a query. Use when the user says 'search for X', 'look up X on YouTube', 'Google X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "engine": {"type": "string", "description": "Search engine: 'google' (default), 'youtube', 'bing', 'duckduckgo'."},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_active_tab",
            "description": "Get the URL and title of the currently active browser tab.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "browser_action",
            "description": "Perform a browser action: new_tab, close_tab, back, forward, reload, scroll_down, scroll_up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of: new_tab, close_tab, back, forward, reload, scroll_down, scroll_up.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_alarm",
            "description": (
                "Set a macOS Clock alarm (mobiletimerd plist). "
                "Use when the user says 'alarm', 'wake me at X', or a clock time. "
                "Default is one-time. When the user asks for repeating days, include their exact repeat phrase in "
                "the ``time`` argument (e.g. '1:50 AM every Friday and Saturday'), not only the clock time—otherwise "
                "repeat is lost. Never infer repeat from the clock time alone."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": (
                            "When to fire — include the SAME natural-language repeat wording the user used "
                            "when they asked for repeating alarms (e.g. '1:50 AM repeats every Friday and Saturday'). "
                            "Plain '1:50 AM' loses repeat info. Examples: '7:30 AM', 'in 2 hours', "
                            "'Wednesday 9 PM', '1 PM every Wednesday'. "
                            "Prefer one set_alarm call with all repeat days in this string rather than multiple calls."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Short label. For repeating alarms you may echo repeat days here if needed; "
                            "repeat scheduling is parsed from time + label together."
                        ),
                        "default": "JARVIS Alarm",
                    },
                },
                "required": ["time"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_alarms",
            "description": "List upcoming alarms and reminders from the macOS Reminders app.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "cancel_alarm",
            "description": (
                "Delete alarm(s) from the macOS Clock app (mobiletimerd). "
                "Match by clock time (add weekday if needed). "
                "Use all_alarms or phrases like 'delete all alarms' to remove every Clock alarm."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "time": {
                        "type": "string",
                        "description": "Alarm time to remove, e.g. '1:20 PM' or '1:20 PM Sunday'.",
                    },
                    "label": {
                        "type": "string",
                        "description": "Optional extra words (e.g. weekday) combined with time when cancelling.",
                    },
                    "all_alarms": {
                        "type": "boolean",
                        "description": "If true, delete every alarm in the Clock app.",
                    },
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "stopwatch",
            "description": "Control a named stopwatch: start, stop, check elapsed time, or reset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "'start', 'stop', 'check', or 'reset'.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Stopwatch name (default: 'main').",
                        "default": "main",
                    },
                },
                "required": ["action"],
            },
        },
    },
]


def _fs_root() -> Path:
    raw = os.environ.get("JARVIS_FS_ROOT", "")
    if raw.strip():
        return Path(raw).expanduser().resolve()
    return (Path.home() / "Documents" / "JARVIS").resolve()


def _safe_path(rel: str) -> Path:
    root = _fs_root()
    root.mkdir(parents=True, exist_ok=True)
    # Strip leading slashes so paths stay relative.
    rel_clean = rel.replace("\\", "/").lstrip("/")
    candidate = (root / rel_clean).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the JARVIS workspace") from exc
    return candidate


def _parse_tool_args(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


async def run_tool(name: str, arguments_json: str) -> str:
    """Execute a tool by name; returns a string for the model (or an error note)."""
    args = _parse_tool_args(arguments_json)
    try:
        if name == "web_search":
            return await _web_search(str(args.get("query", "")))
        if name == "list_directory":
            return _list_directory(str(args.get("path", ".") or "."), int(args.get("max_entries", 40) or 40))
        if name == "read_file":
            return _read_file(str(args.get("path", "")), int(args.get("max_bytes", 65536) or 65536))
        if name == "calendar_upcoming":
            return _calendar_upcoming(int(args.get("days_ahead", 14) or 14), int(args.get("max_events", 20) or 20))
        if name == "set_reminder":
            return await _set_reminder(
                str(args.get("message", "") or ""),
                float(args.get("minutes", 0) or 0),
                str(args.get("name", "") or "").strip() or None,
            )
        if name == "open_app":
            return _open_app(str(args.get("app_name", "")))
        if name == "set_volume":
            mute = args.get("mute")
            if mute is True:
                return _set_mute(True)
            if mute is False:
                return _set_mute(False)
            return _set_volume(int(args.get("percent", 50) or 50))
        if name == "set_brightness":
            return _set_brightness(int(args.get("percent", 50) or 50))
        if name == "get_clipboard":
            return _get_clipboard()
        if name == "set_clipboard":
            return _set_clipboard(str(args.get("text", "")))
        if name == "take_screenshot":
            return await _take_screenshot()
        if name == "set_alarm":
            _lbl = args.get("label") or "JARVIS Alarm"
            return _set_alarm(str(args.get("time", "")), str(_lbl))
        if name == "list_alarms":
            return _list_alarms()
        if name == "cancel_alarm":
            if args.get("all_alarms") is True:
                return _cancel_all_alarms()
            q = " ".join(
                str(x).strip()
                for x in (args.get("time"), args.get("label"))
                if x is not None and str(x).strip()
            ).strip()
            return _cancel_alarm(q or str(args.get("label") or args.get("time") or ""))
        if name == "stopwatch":
            return _stopwatch(str(args.get("action", "")), str(args.get("name", "main") or "main"))
        if name == "open_url":
            return _open_url(str(args.get("url", "")), str(args.get("browser", "") or ""))
        if name == "browser_search":
            return _browser_search(str(args.get("query", "")), str(args.get("engine", "google") or "google"))
        if name == "get_active_tab":
            return _get_active_tab()
        if name == "browser_action":
            return _browser_action(str(args.get("action", "")))
    except Exception as exc:  # noqa: BLE001
        logger.exception("tool %s failed", name)
        return f"Tool error ({name}): {exc}"
    return f"Unknown tool: {name}"


async def _web_search(query: str) -> str:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return (
            "Web search is not configured. Set TAVILY_API_KEY in the backend `.env`."
        )
    q = query.strip()
    if not q:
        return "Empty search query."

    url = "https://api.tavily.com/search"
    payload = {
        "api_key": key,
        "query": q,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post(url, json=payload)
        r.raise_for_status()
        data = r.json()

    lines: list[str] = []
    if ans := data.get("answer"):
        lines.append(f"Summary: {ans}")
    for item in data.get("results") or []:
        title = item.get("title") or ""
        u = item.get("url") or ""
        content = (item.get("content") or "")[:400]
        lines.append(f"- {title}\n  {u}\n  {content}")
    if not lines:
        return "No results returned."
    return "\n".join(lines)


def _list_directory(rel: str, max_entries: int) -> str:
    cap = max(1, min(max_entries, 100))
    path = _safe_path(rel)
    if not path.exists():
        return f"Path does not exist: {rel}"
    if not path.is_dir():
        return f"Not a directory: {rel}"
    all_entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    names = all_entries[:cap]
    rows = [f"{'DIR ' if p.is_dir() else 'FILE'} {p.name}" for p in names]
    if not rows:
        return "(empty directory)"
    more = len(all_entries) - len(names)
    tail = f"\n… and {more} more (raise max_entries)." if more > 0 else ""
    return f"Listing of {rel} under workspace {_fs_root()}:\n" + "\n".join(rows) + tail


def _read_file(rel: str, max_bytes: int) -> str:
    cap = max(256, min(max_bytes, 512_000))
    if not rel.strip():
        return "Missing path."
    path = _safe_path(rel)
    if not path.exists():
        return f"File not found: {rel}"
    if not path.is_file():
        return f"Not a file: {rel}"
    full = path.read_bytes()
    data = full[:cap]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"File is not valid UTF-8 (showing first {cap} bytes as hex): {data[:64].hex()}"
    if len(full) > cap:
        text += "\n… [truncated]"
    return text


def _calendar_upcoming(days_ahead: int, max_events: int) -> str:
    ics_path = os.environ.get("JARVIS_CALENDAR_ICS", "").strip()
    if not ics_path:
        return (
            "No calendar file configured. Set JARVIS_CALENDAR_ICS to a local .ics path."
        )
    path = Path(ics_path).expanduser().resolve()
    if not path.is_file():
        return f"Calendar file not found: {path}"

    try:
        from icalendar import Calendar  # noqa: PLC0415
    except ImportError:
        return "Calendar support requires `icalendar` (uv sync --extra memory)."

    raw = path.read_bytes()
    try:
        cal = Calendar.from_ical(raw)
    except Exception as exc:  # noqa: BLE001
        return f"Failed to parse .ics: {exc}"

    now = datetime.now(timezone.utc)
    horizon = now + timedelta(days=max(1, min(days_ahead, 365)))

    events: list[tuple[datetime, str]] = []
    for component in cal.walk():
        if component.name != "VEVENT":
            continue
        start = component.get("dtstart")
        if start is None:
            continue
        dt = start.dt
        if not isinstance(dt, datetime):
            # All-day events may be date-only
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        summary = str(component.get("summary", "Event"))
        if now <= dt <= horizon:
            events.append((dt, summary))

    events.sort(key=lambda x: x[0])
    events = events[: max(1, min(max_events, 50))]
    if not events:
        return f"No upcoming events in the next {days_ahead} days (from {path})."

    lines = [f"Upcoming events from {path.name}:"]
    for dt, title in events:
        lines.append(f"- {dt.isoformat()} — {title}")
    return "\n".join(lines)


def _osascript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def _add_macos_reminders_item(title: str, notes: str, delay_seconds: float) -> tuple[bool, str]:
    """Create an entry in the default Reminders list (due ``delay_seconds`` from now).

    This is separate from the Jarvis HUD: users see it in Apple's Reminders app and get
    system notifications even if the browser tab is closed — same idea as the old
    alarm fallback that used AppleScript when Clock could not be written.
    """
    t = _osascript_escape(title.strip() or "Reminder")[:800]
    n = _osascript_escape(notes.strip())[:4000] if notes.strip() else ""
    sec = max(int(delay_seconds), 60)
    body_set = f'    set body of r to "{n}"\n' if n else ""
    script = f'''tell application "Reminders"
    set myList to default list
    set r to make new reminder at end of myList
    set name of r to "{t}"
{body_set}    set due date of r to (current date) + {sec}
    set remind me date of r to (current date) + {sec}
end tell'''
    result = subprocess.run(["osascript", "-e", script], capture_output=True, text=True, timeout=15)
    if result.returncode != 0:
        err = (result.stderr or result.stdout or "osascript failed").strip()
        logger.warning("macOS Reminders item failed: %s", err[:300])
        return False, err[:200]
    return True, ""


async def _set_reminder(message: str, minutes: float, title: str | None = None) -> str:
    body = message.strip()
    name = (title or "").strip()
    if not body and not name:
        return "Reminder needs a message or a name (what to remind you about)."
    if minutes <= 0:
        return "Delay must be positive."

    display_body = body if body else name
    list_title = name if name else display_body
    list_notes = body if (name and body) else ""

    delay_seconds = minutes * 60.0
    sys_ok, sys_err = _add_macos_reminders_item(list_title, list_notes, delay_seconds)

    payload: dict[str, Any] = {"type": "reminder", "speak": True}
    if name and body:
        payload["name"] = name
        payload["message"] = body
    else:
        payload["message"] = display_body

    async def _fire() -> None:
        try:
            await asyncio.sleep(delay_seconds)
            logger.info("Reminder firing (HUD): %s", payload)
            if _broadcast_fn is not None:
                await _broadcast_fn(payload)
            else:
                logger.warning("Reminder fired but no broadcast_fn configured")
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Reminder HUD broadcast failed")

    asyncio.create_task(_fire())
    mins_str = f"{minutes:.4g}"
    base = (
        f"Reminder '{name}' set — I'll remind you about '{body}' in {mins_str} minute(s)."
        if name and body
        else f"Reminder set — I'll remind you about '{display_body}' in {mins_str} minute(s)."
    )
    if sys_ok:
        return (
            f"{base} Also added to Apple's Reminders app (due in {mins_str} minutes) so you get a "
            "system notification even if this session is closed."
        )
    return (
        f"{base} (Could not add to Apple's Reminders app — {sys_err}. "
        "Jarvis will still try to alert here when the web UI is connected.)"
    )


def _open_app(app_name: str) -> str:
    name = app_name.strip()
    if not name:
        return "App name cannot be empty."
    try:
        subprocess.run(["open", "-a", name], check=True, capture_output=True, timeout=10)
        return f"Opened {name}."
    except subprocess.CalledProcessError:
        return f"Could not open '{name}'. Make sure the app is installed in /Applications."
    except FileNotFoundError:
        return "open command not available."


def _osascript(script: str) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip()


def _set_volume(percent: int) -> str:
    level = max(0, min(percent, 100))
    _osascript(f"set volume output volume {level}")
    return f"Volume set to {level}%."


def _set_mute(muted: bool) -> str:
    _osascript(f"set volume output muted {str(muted).lower()}")
    return "Muted." if muted else "Unmuted."


def _set_brightness(percent: int) -> str:
    level = max(0, min(percent, 100))
    frac = round(level / 100, 2)
    # Try the `brightness` CLI first (brew install brightness).
    try:
        result = subprocess.run(
            ["brightness", str(frac)],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return f"Brightness set to {level}%."
    except FileNotFoundError:
        pass
    # Fallback: System Events key simulation (changes by steps, not exact).
    _osascript(
        f'tell application "System Events" to set brightness of every display to {frac}'
    )
    return f"Brightness set to approximately {level}%."


def _get_clipboard() -> str:
    try:
        result = subprocess.run(
            ["pbpaste"], capture_output=True, text=True, timeout=5,
        )
        text = result.stdout
        if not text:
            return "(Clipboard is empty)"
        if len(text) > 2000:
            return text[:2000] + "\n… [truncated]"
        return text
    except FileNotFoundError:
        return "pbpaste not available."


def _set_clipboard(text: str) -> str:
    try:
        subprocess.run(
            ["pbcopy"], input=text, text=True, check=True, timeout=5,
        )
        preview = text[:60].replace("\n", "↵")
        return f"Copied to clipboard: {preview!r}"
    except FileNotFoundError:
        return "pbcopy not available."


async def _take_screenshot() -> str:
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if not api_key:
        return "Screenshot vision requires ANTHROPIC_API_KEY in the backend .env."

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        proc = await asyncio.create_subprocess_exec(
            "screencapture", "-x", tmp_path,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=10)

        import base64

        image_data = Path(tmp_path).read_bytes()
        b64 = base64.standard_b64encode(image_data).decode()

        import anthropic

        client = anthropic.AsyncAnthropic(api_key=api_key)
        response = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=512,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": "image/png",
                                "data": b64,
                            },
                        },
                        {
                            "type": "text",
                            "text": (
                                "Describe what is currently visible on this screen "
                                "in 2-4 concise sentences. Focus on the active application "
                                "and any notable content."
                            ),
                        },
                    ],
                }
            ],
        )
        return response.content[0].text  # type: ignore[attr-defined]
    finally:
        Path(tmp_path).unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Alarms, reminders (Reminders.app) and stopwatch
# ---------------------------------------------------------------------------

# Weekday names — used by alarm parsing and repeat bitmask (must stay aligned).
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


def _extract_clock_hm(cleaned: str) -> tuple[int, int] | None:
    """Parse local hour (0–23) and minute from a filler-stripped fragment.

    Prefers explicit colon/spaced forms with am/pm so long phrases like
    'set an alarm 1 50 am …' do not match a stray leading digit as the hour.
    """
    s = cleaned.strip()
    # H:MM am/pm
    m = re.search(r"\b(\d{1,2}):(\d{2})\s*(am|pm)\b", s, re.I)
    if m:
        h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
    else:
        # H MM am/pm (e.g. 1 50 am)
        m = re.search(r"\b(\d{1,2})\s+(\d{1,2})\s*(am|pm)\b", s, re.I)
        if m:
            h, mn, ap = int(m.group(1)), int(m.group(2)), m.group(3).lower()
        else:
            # H am/pm (e.g. 1 pm)
            m = re.search(r"\b(\d{1,2})\s*(am|pm)\b", s, re.I)
            if m:
                h, mn, ap = int(m.group(1)), 0, m.group(2).lower()
            else:
                # 24-hour H:MM
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
    """Return (target_datetime_local, seconds_from_now).

    Handles relative ("in 2 hours") and absolute ("7:30 AM", "tomorrow at 9 PM",
    "tuesday 4 PM", "4 PM on friday").  Day names are stripped before time parsing
    so the LLM can safely include them in the time string.
    Raises ValueError if unparseable.
    """
    raw = time_str.strip().lower()
    now = _local_now()

    # Relative: "in X seconds/minutes/hours"
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

    # Detect "tomorrow"
    is_tomorrow = "tomorrow" in raw
    cleaned = re.sub(r"tomorrow\s*(at\s*)?", "", raw).strip()

    # Strip day names so "tuesday 4 PM" or "4 PM on friday" both reduce to just the time
    cleaned = re.sub(_DAY_NAMES_PATTERN, "", cleaned)
    # Strip filler words left over after day removal
    cleaned = re.sub(r"\b(at|on|for|only|each|every)\b", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    hm = _extract_clock_hm(cleaned)
    if hm is None:
        raise ValueError(f"Cannot parse time expression: {time_str!r}")
    h, mn = hm

    # Next occurrence of (weekday + clock time). Single weekday only; otherwise wall-clock.
    if not is_tomorrow:
        day_tokens = re.findall(_DAY_NAMES_PATTERN, raw)
        uniq_bits = {_DAY_BITS[t] for t in day_tokens if t in _DAY_BITS}
        if len(uniq_bits) == 1:
            bit = next(iter(uniq_bits))
            want_wd = bit.bit_length() - 1  # Mon=1→0 … Sun=64→6
            candidate = now.replace(hour=h, minute=mn, second=0, microsecond=0)
            d = (want_wd - candidate.weekday()) % 7
            if d == 0 and candidate <= now:
                d = 7
            target = candidate + timedelta(days=d)
            seconds = max(1, int((target - now).total_seconds()))
            return target, seconds

    target = now.replace(hour=h, minute=mn, second=0, microsecond=0)
    if is_tomorrow or target <= now:
        target += timedelta(days=1)
    seconds = max(1, int((target - now).total_seconds()))
    return target, seconds


# MTAlarmRepeatSchedule — Apple MobileTimer uses **Monday-first** bits (not Sunday-first):
#   Mon=1 Tue=2 Wed=4 Thu=8 Fri=16 Sat=32 Sun=64  (sum 127 = every day)
# Using Sun=1… would shift every weekday label (e.g. "Monday" wrote value 2 → showed as Tuesday).
_REPEAT_NEVER = 0
_REPEAT_EVERY_DAY = 127
_REPEAT_WEEKDAYS = 31  # Mon–Fri (1+2+4+8+16)
_REPEAT_WEEKENDS = 96  # Sat+Sun (32+64)


def _distinct_weekday_bits(text: str) -> set[int]:
    return {_DAY_BITS[t] for t in re.findall(_DAY_NAMES_PATTERN, text.lower()) if t in _DAY_BITS}


def _reminder_alarm_fallback(seconds: int, label: str, time_fmt: str) -> str:
    """Only when ``defaults`` / mobiletimerd fails — user asked for Clock, not Reminders."""
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


def _restart_mobiletimerd() -> None:
    """SIGKILL mobiletimerd so launchd relaunches it and reloads ``defaults`` plist."""
    pid = subprocess.run(["pgrep", "mobiletimerd"], capture_output=True, text=True).stdout.strip()
    if pid:
        subprocess.run(["kill", "-9", pid], capture_output=True, timeout=5)


def _write_mt_alarm(hour: int, minute: int, repeat: int = _REPEAT_EVERY_DAY) -> bool:
    """Write an alarm to mobiletimerd's plist and restart the daemon so Clock.app
    picks it up immediately.

    Format reverse-engineered from native mobiletimerd writes — no MTAlarmTitle
    (that extra key breaks initFromDeserializer: causing SIGABRT crashes).
    Restarts mobiletimerd via SIGKILL so launchd relaunches it fresh; safe as long
    as launchd runs < throttle grace limit (10).  Never use SIGHUP (causes exit
    without launchd restart) or notifyutil reset (wipes all alarms).
    """
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


def _set_alarm(time_str: str, label: str = "JARVIS Alarm") -> str:
    if not time_str.strip():
        return "Please specify a time for the alarm."
    label = label.strip() or "JARVIS Alarm"

    try:
        target, secs_from_now = _parse_alarm_time(time_str)
    except ValueError as exc:
        return f"Could not understand the time '{time_str}': {exc}"

    hour, minute = target.hour, target.minute
    time_fmt = target.strftime("%-I:%M %p")

    # Detect repeat intent from the label/time string
    combined = (time_str + " " + label).lower()
    if any(w in combined for w in ("once", "one time", "one-time", "today only", "just today")):
        repeat = _REPEAT_NEVER
        repeat_desc = "once"
    elif any(w in combined for w in ("every day", "everyday", "daily")):
        repeat = _REPEAT_EVERY_DAY
        repeat_desc = "every day"
    elif any(w in combined for w in ("weekday", "weekdays", "work day", "monday to friday")):
        repeat = _REPEAT_WEEKDAYS
        repeat_desc = "weekdays"
    elif any(w in combined for w in ("weekend", "weekends")):
        repeat = _REPEAT_WEEKENDS
        repeat_desc = "weekends"
    else:
        # Weekly repeat only from explicit "every/each/repeat …" phrases.
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
                canon = next(
                    k for k, v in _DAY_BITS.items() if v == _DAY_BITS[token] and len(k) > 3
                )
                day_names.append(canon.capitalize())
        if day_mask:
            repeat = day_mask
            repeat_desc = "/".join(day_names)
        else:
            repeat = _REPEAT_NEVER
            repeat_desc = "once"

    # Named weekday + no explicit "once": Clock only stores weekly repeat or plain hour/minute — use the
    # matching weekday bit so the alarm lives in Clock (not Reminders) and fires on that day.
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
        return (
            f"Alarm set for {time_fmt} ({how}). "
            f"It will appear in the Clock app's Alarms tab."
        )

    return _reminder_alarm_fallback(secs_from_now, label, time_fmt)


def _read_mt_alarms() -> list[dict]:
    """Return parsed alarm list from mobiletimerd global prefs."""
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


def _list_alarms() -> str:
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


def _is_cancel_all_phrase(text: str) -> bool:
    """Natural-language intent to remove every Clock alarm."""
    t = text.strip().lower()
    if not t:
        return False
    if re.search(
        r"\b(delete|remove|cancel|clear|dismiss|drop|erase)\s+all(\s+\w+)*\s+alarms?\b",
        t,
    ):
        return True
    if re.search(r"\b(delete|remove|cancel|clear)\s+every\s+alarm\b", t):
        return True
    if t in (
        "all alarms",
        "delete all alarms",
        "cancel all alarms",
        "remove all alarms",
        "clear all alarms",
        "delete all my alarms",
        "cancel all my alarms",
    ):
        return True
    if ("alarm" in t or "alarms" in t) and "all" in t:
        if any(w in t for w in ("delete", "remove", "cancel", "clear", "dismiss", "drop", "erase")):
            return True
    return False


def _cancel_all_alarms() -> str:
    """Clear MTAlarms list in mobiletimerd prefs and restart the daemon."""
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


def _cancel_alarm(query: str) -> str:
    """Remove alarm(s) from Clock whose hour/minute match; optionally require repeat bitmask."""
    query = query.strip()
    if not query:
        return "Please specify the alarm time to cancel (e.g. '7 AM' or '1:20 PM Sunday')."
    if _is_cancel_all_phrase(query):
        return _cancel_all_alarms()
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
        sched = int(inner.get("MTAlarmRepeatSchedule") or 0)
        return sched == repeat_filter

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
            if repeat_filter is None:
                hint = (
                    f" If several alarms share that time, include the day "
                    f"(e.g. 'cancel {tf} Sunday')."
                )
            else:
                hint = " Check the time and day (repeat schedule must match exactly)."
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


# In-memory stopwatch store: name → (start_ts, stopped_elapsed_s | None)
_stopwatches: dict[str, tuple[float, float | None]] = {}


def _stopwatch(action: str, name: str = "main") -> str:
    name = name.strip() or "main"
    now = _time.monotonic()
    entry = _stopwatches.get(name)

    if action == "start":
        if entry and entry[1] is None:
            elapsed = now - entry[0]
            return f"Stopwatch '{name}' is already running ({_fmt_elapsed(elapsed)})."
        _stopwatches[name] = (now, None)
        return f"Stopwatch '{name}' started."

    if action == "stop":
        if not entry:
            return f"Stopwatch '{name}' hasn't been started."
        if entry[1] is not None:
            return f"Stopwatch '{name}' is already stopped at {_fmt_elapsed(entry[1])}."
        elapsed = now - entry[0]
        _stopwatches[name] = (entry[0], elapsed)
        return f"Stopwatch '{name}' stopped at {_fmt_elapsed(elapsed)}."

    if action == "check":
        if not entry:
            return f"No stopwatch named '{name}' exists. Say 'start stopwatch' to begin."
        if entry[1] is not None:
            return f"Stopwatch '{name}' stopped at {_fmt_elapsed(entry[1])}."
        elapsed = now - entry[0]
        return f"Stopwatch '{name}' is running — {_fmt_elapsed(elapsed)} elapsed."

    if action == "reset":
        if name in _stopwatches:
            del _stopwatches[name]
        return f"Stopwatch '{name}' reset."

    return f"Unknown stopwatch action '{action}'. Use: start, stop, check, reset."


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


# ---------------------------------------------------------------------------
# Browser control
# ---------------------------------------------------------------------------

_BROWSER_APP_NAMES: dict[str, str] = {
    "chrome":   "Google Chrome",
    "safari":   "Safari",
    "firefox":  "Firefox",
    "arc":      "Arc",
    "brave":    "Brave Browser",
    "edge":     "Microsoft Edge",
    "opera":    "Opera",
}

# Ordered list used when detecting which browser is frontmost.
_KNOWN_BROWSERS = [
    "Google Chrome", "Safari", "Arc", "Firefox",
    "Brave Browser", "Microsoft Edge", "Opera",
]


def _frontmost_browser() -> str:
    """Return the name of the frontmost known browser, or 'Safari' as fallback."""
    script = (
        'tell application "System Events" to return name of '
        'first application process whose frontmost is true'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5,
        )
        front = result.stdout.strip()
        for b in _KNOWN_BROWSERS:
            if b.lower() in front.lower():
                return b
    except Exception:  # noqa: BLE001
        pass
    return "Safari"


def _open_url(url: str, browser: str = "") -> str:
    url = url.strip()
    if not url:
        return "URL cannot be empty."
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url

    if browser:
        app = _BROWSER_APP_NAMES.get(browser.lower(), browser)
        try:
            subprocess.run(["open", "-a", app, url], check=True, capture_output=True, timeout=10)
            return f"Opened {url} in {app}."
        except subprocess.CalledProcessError:
            pass  # fall through to default

    subprocess.run(["open", url], check=True, capture_output=True, timeout=10)
    return f"Opened {url}."


def _browser_search(query: str, engine: str = "google") -> str:
    from urllib.parse import quote_plus  # noqa: PLC0415
    q = quote_plus(query.strip())
    urls: dict[str, str] = {
        "google":     f"https://www.google.com/search?q={q}",
        "youtube":    f"https://www.youtube.com/results?search_query={q}",
        "bing":       f"https://www.bing.com/search?q={q}",
        "duckduckgo": f"https://duckduckgo.com/?q={q}",
    }
    url = urls.get(engine.lower(), urls["google"])
    return _open_url(url)


def _get_active_tab() -> str:
    checks = [
        ("Google Chrome", (
            'tell application "Google Chrome"\n'
            '  set t to active tab of front window\n'
            '  return (URL of t) & " ||| " & (title of t)\n'
            'end tell'
        )),
        ("Safari", (
            'tell application "Safari"\n'
            '  set t to current tab of front window\n'
            '  return (URL of t) & " ||| " & (name of t)\n'
            'end tell'
        )),
        ("Arc", (
            'tell application "Arc"\n'
            '  set t to active tab of front window\n'
            '  return (URL of t) & " ||| " & (title of t)\n'
            'end tell'
        )),
    ]
    for browser, script in checks:
        try:
            guard = (
                f'tell application "System Events"\n'
                f'  if (count of (processes whose name is "{browser}")) > 0 then\n'
                f'    {script}\n'
                f'  end if\n'
                f'end tell'
            )
            r = subprocess.run(
                ["osascript", "-e", guard],
                capture_output=True, text=True, timeout=6,
            )
            out = r.stdout.strip()
            if out and " ||| " in out:
                url, title = out.split(" ||| ", 1)
                return f'Active tab: "{title}"\nURL: {url}'
        except Exception:  # noqa: BLE001
            continue
    return "No supported browser is open (tried Chrome, Safari, Arc)."


def _browser_action(action: str) -> str:
    browser = _frontmost_browser()

    def _js(code: str) -> str:
        """Run JavaScript in the frontmost tab."""
        if browser == "Safari":
            return (
                f'tell application "Safari" to do JavaScript "{code}" '
                f'in current tab of front window'
            )
        return (
            f'tell application "{browser}" to execute '
            f'front window\'s active tab javascript "{code}"'
        )

    scripts: dict[str, str] = {
        "new_tab": (
            f'tell application "{browser}" to make new tab at end of tabs of front window'
            if browser != "Safari"
            else 'tell application "Safari" to tell front window to make new tab'
        ),
        "close_tab": (
            'tell application "Safari" to close current tab of front window'
            if browser == "Safari"
            else f'tell application "{browser}" to close active tab of front window'
        ),
        "back":    _js("history.back()"),
        "forward": _js("history.forward()"),
        "reload":  _js("location.reload()"),
        "scroll_down": _js("window.scrollBy(0,600)"),
        "scroll_up":   _js("window.scrollBy(0,-600)"),
    }

    script = scripts.get(action)
    if not script:
        return f"Unknown browser action '{action}'. Valid actions: {', '.join(scripts)}."

    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=8,
    )
    if result.returncode != 0 and result.stderr.strip():
        return f"Browser action failed: {result.stderr.strip()}"

    labels = {
        "new_tab": "Opened a new tab.",
        "close_tab": "Closed the current tab.",
        "back": "Navigated back.",
        "forward": "Navigated forward.",
        "reload": "Page reloaded.",
        "scroll_down": "Scrolled down.",
        "scroll_up": "Scrolled up.",
    }
    return labels.get(action, f"Done: {action}.")
