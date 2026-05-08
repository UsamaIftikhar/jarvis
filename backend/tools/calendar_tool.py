from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .registry import REGISTRY, ToolEntry


async def _calendar_upcoming(args: dict[str, Any]) -> str:
    days_ahead = int(args.get("days_ahead", 14) or 14)
    max_events = int(args.get("max_events", 20) or 20)

    ics_path = os.environ.get("JARVIS_CALENDAR_ICS", "").strip()
    if not ics_path:
        return "No calendar file configured. Set JARVIS_CALENDAR_ICS to a local .ics path."
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


REGISTRY.register(ToolEntry(
    definition={
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
    handler=_calendar_upcoming,
    thinking_label="Checking calendar…",
))
