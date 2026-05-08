from __future__ import annotations

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable
from typing import Any

from ._util import osascript_escape
from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.tools")

_broadcast_fn: Callable[[dict[str, Any]], Awaitable[None]] | None = None


def set_broadcast_fn(fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    global _broadcast_fn
    _broadcast_fn = fn


def _add_macos_reminder(title: str, notes: str, delay_seconds: float) -> tuple[bool, str]:
    t = osascript_escape(title.strip() or "Reminder")[:800]
    n = osascript_escape(notes.strip())[:4000] if notes.strip() else ""
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


async def _set_reminder(args: dict[str, Any]) -> str:
    body = str(args.get("message", "") or "").strip()
    name = str(args.get("name", "") or "").strip()
    minutes = float(args.get("minutes", 0) or 0)

    if not body and not name:
        return "Reminder needs a message or a name."
    if minutes <= 0:
        return "Delay must be positive."

    display_body = body if body else name
    list_title = name if name else display_body
    list_notes = body if (name and body) else ""
    delay_seconds = minutes * 60.0

    sys_ok, sys_err = _add_macos_reminder(list_title, list_notes, delay_seconds)

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


REGISTRY.register(ToolEntry(
    definition={
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
                        "description": "Optional short title shown with the reminder.",
                    },
                },
                "required": ["minutes"],
            },
        },
    },
    handler=_set_reminder,
    thinking_label="Setting reminder…",
    terminal=True,
    help_hint="in-session HUD + optional title; at least one of message/name; speak on fire",
))
