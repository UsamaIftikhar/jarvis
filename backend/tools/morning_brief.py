"""Morning brief tool — triggered when the user says 'good morning'.

Fetches today's calendar, current weather, and top-3 unread emails in
parallel, caches the result for the day, and returns raw data for JARVIS
to distil into a spoken summary.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from pathlib import Path
from typing import Any

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.tools")

_STATE_PATH = Path(__file__).resolve().parent.parent / "data" / "morning_brief_state.json"


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def _call(name: str, args: dict[str, Any]) -> str:
    """Call a registered tool by name without importing the tools package (avoids circular)."""
    entry = REGISTRY.get(name)
    if entry is None:
        return f"(tool {name!r} unavailable)"
    try:
        return await entry.handler(args)
    except Exception as exc:
        logger.warning("morning_brief sub-tool %s failed: %s", name, exc)
        return "(unavailable)"


async def _morning_brief(_args: dict[str, Any]) -> str:
    from user_profile import load_profile  # local import — avoid top-level circular

    today = date.today().isoformat()
    st = _load_state()

    # Return cached brief if already delivered today
    if st.get("date") == today and st.get("brief"):
        return f"[already_briefed_today]\n{st['brief']}"

    p = load_profile()
    city = (p.city or "").strip() or "your city"

    cal, wx, emails = await asyncio.gather(
        _call("calendar_upcoming", {"days_ahead": 1, "max_events": 8}),
        _call("web_search", {"query": f"weather today {city}"}),
        _call("gmail__search_emails", {"query": "is:unread", "maxResults": 3}),
    )

    brief = (
        f"[CALENDAR]\n{cal[:1500]}\n\n"
        f"[WEATHER — {city}]\n{wx[:800]}\n\n"
        f"[TOP UNREAD EMAILS]\n{emails[:1500]}"
    )

    st["date"] = today
    st["brief"] = brief
    _save_state(st)
    return brief


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "morning_brief",
            "description": (
                "Deliver the daily morning briefing: today's calendar events, "
                "current weather, and the top 3 unread emails. "
                "Call this when the user says 'good morning', 'morning update', "
                "or asks for their morning brief. Data is fresh-fetched once per day."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_morning_brief,
    thinking_label="Preparing your morning brief…",
    terminal=True,
    help_hint="once-daily: calendar + weather + top 3 emails",
))
