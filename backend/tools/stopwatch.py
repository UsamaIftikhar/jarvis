from __future__ import annotations

import time as _time
from typing import Any

from .registry import REGISTRY, ToolEntry

_stopwatches: dict[str, tuple[float, float | None]] = {}


def _fmt_elapsed(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h}h {m}m {sec}s"
    if m:
        return f"{m}m {sec}s"
    return f"{sec}s"


async def _stopwatch(args: dict[str, Any]) -> str:
    action = str(args.get("action", ""))
    name = str(args.get("name", "main") or "main").strip() or "main"
    now = _time.monotonic()
    entry = _stopwatches.get(name)

    if action == "start":
        if entry and entry[1] is None:
            return f"Stopwatch '{name}' is already running ({_fmt_elapsed(now - entry[0])})."
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
        return f"Stopwatch '{name}' is running — {_fmt_elapsed(now - entry[0])} elapsed."

    if action == "reset":
        if name in _stopwatches:
            del _stopwatches[name]
        return f"Stopwatch '{name}' reset."

    return f"Unknown stopwatch action '{action}'. Use: start, stop, check, reset."


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "stopwatch",
            "description": "Control a named stopwatch: start, stop, check elapsed time, or reset.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {"type": "string", "description": "'start', 'stop', 'check', or 'reset'."},
                    "name": {"type": "string", "description": "Stopwatch name (default: 'main').", "default": "main"},
                },
                "required": ["action"],
            },
        },
    },
    handler=_stopwatch,
    thinking_label="Stopwatch…",
    terminal=True,
))
