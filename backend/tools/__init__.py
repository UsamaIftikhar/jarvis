"""JARVIS tool registry — import this package to get all tools wired up.

Adding a built-in tool:
  1. Create backend/tools/mymodule.py
  2. Define async handler(s) and call REGISTRY.register(ToolEntry(...)) at module level
  3. Import it here — agent_loop picks it up automatically.

Adding an MCP integration:
  Edit backend/mcp_servers.json — no Python code needed.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

# Import all tool modules — each one self-registers into REGISTRY on import.
from . import (
    alarms,
    calendar_tool,
    filesystem,
    gdrive,
    github_tools,
    morning_brief,
    notes,
    reminders,
    stopwatch,
    system,
    vision,
    web,
)
from . import mcp_loader
from .registry import REGISTRY

logger = logging.getLogger("jarvis.tools")

# Computed once after all modules have registered their tools.
TOOL_DEFINITIONS: list[dict[str, Any]] = REGISTRY.all_definitions()


def configure_tools(broadcast_fn: Callable[[dict[str, Any]], Awaitable[None]]) -> None:
    reminders.set_broadcast_fn(broadcast_fn)


def _parse_args(raw: str) -> dict[str, Any]:
    if not raw or not raw.strip():
        return {}
    try:
        out = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return out if isinstance(out, dict) else {}


async def run_tool(name: str, arguments_json: str) -> str:
    entry = REGISTRY.get(name)
    if not entry:
        return f"Unknown tool: {name}"
    args = _parse_args(arguments_json)
    try:
        return await entry.handler(args)
    except Exception as exc:
        logger.exception("tool %s failed", name)
        return f"Tool error ({name}): {exc}"
