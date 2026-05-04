"""ReAct-style agent loop (Layer 4) — JSON decisions, tool calls, then streamed final answer.

Reasoning steps are not user-visible; callers send ``thinking_step`` over WebSocket.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from llm import DeepSeekClient
from tools import run_tool

logger = logging.getLogger("jarvis.agent")

MAX_STEPS = 6
LOOP_TIMEOUT_S = 15.0

TOOL_HELP = """
Available tools — action must be EXACTLY one of these names, or final_answer:
- web_search — args: {"query": string}
- list_directory — args: {"path"?: string, "max_entries"?: number}
- read_file — args: {"path": string, "max_bytes"?: number}
- calendar_upcoming — args: {"days_ahead"?: number, "max_events"?: number}
- set_reminder — args: {"message"?: string, "minutes": number, "name"?: string}  ← in-session HUD + optional title; at least one of message/name; speak on fire
- open_app — args: {"app_name": string}
- set_volume — args: {"percent"?: number, "mute"?: boolean}
- set_brightness — args: {"percent": number}
- get_clipboard — args: {}
- set_clipboard — args: {"text": string}
- take_screenshot — args: {}
- open_url — args: {"url": string, "browser"?: string}
- browser_search — args: {"query": string, "engine"?: "google"|"youtube"|"bing"|"duckduckgo"}
- get_active_tab — args: {}
- browser_action — args: {"action": "new_tab"|"close_tab"|"back"|"forward"|"reload"|"scroll_down"|"scroll_up"}
- set_alarm — args: {"time": string, "label"?: string}  ← Puts an alarm in the Clock app; include repeat phrases in ``time`` when the user asked for them. Do not split repeat days across calls.
- list_alarms — args: {}
- cancel_alarm — args: {"time"?: string, "label"?: string, "all_alarms"?: boolean}  ← set all_alarms true or phrase "delete/cancel all alarms" to clear every Clock alarm; otherwise merge time+label.
- stopwatch — args: {"action": "start"|"stop"|"check"|"reset", "name"?: string}
- final_answer — args: null (when you can answer without more tools)
"""

# Tools that perform a one-shot side effect — break the loop immediately after
# a single call so the model can't repeat the action (e.g. opening 6 tabs).
_TERMINAL_TOOLS: frozenset[str] = frozenset({
    "open_app",
    "set_volume",
    "set_brightness",
    "set_clipboard",
    "set_reminder",
    "open_url",
    "browser_search",
    "browser_action",
    "set_alarm",
    "cancel_alarm",
    "stopwatch",
})

_THINKING_LABELS = {
    "web_search": "Searching…",
    "list_directory": "Listing workspace…",
    "read_file": "Reading file…",
    "calendar_upcoming": "Checking calendar…",
    "set_reminder": "Setting reminder…",
    "open_app": "Opening app…",
    "set_volume": "Adjusting volume…",
    "set_brightness": "Adjusting brightness…",
    "get_clipboard": "Reading clipboard…",
    "set_clipboard": "Copying to clipboard…",
    "take_screenshot": "Capturing screen…",
    "open_url": "Opening browser…",
    "browser_search": "Searching in browser…",
    "get_active_tab": "Reading active tab…",
    "browser_action": "Controlling browser…",
    "set_alarm": "Setting alarm…",
    "list_alarms": "Checking alarms…",
    "cancel_alarm": "Cancelling alarm…",
    "stopwatch": "Stopwatch…",
}


def parse_react_json(text: str) -> dict[str, Any]:
    """Parse model output that should be a single JSON object."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        raw = m.group(0)
    return json.loads(raw)


async def _decide(
    client: DeepSeekClient,
    *,
    system: str,
    user_message: str,
    scratchpad: str,
) -> dict[str, Any]:
    prompt = f"""{TOOL_HELP}

Return ONE JSON object only (no markdown):
{{"thought": string, "action": string, "args": object|null}}

If you have enough to answer accurately, set action to "final_answer" and args null.
Otherwise pick one tool.

User message:
{user_message}

Scratchpad:
{scratchpad or "(empty)"}
"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    data = await client.complete(messages, tools=None, temperature=0.3, max_tokens=500)
    msg = data.get("choices", [{}])[0].get("message") or {}
    content = (msg.get("content") or "").strip()
    if not content:
        return {"thought": "", "action": "final_answer", "args": None}
    try:
        return parse_react_json(content)
    except Exception:
        logger.warning("parse_react_json failed: %r", content[:240])
        return {"thought": "parse error", "action": "final_answer", "args": None}


async def run_react_agent(
    *,
    client: DeepSeekClient,
    full_system: str,
    user_message: str,
    history_messages: list[dict[str, Any]],
    on_thinking_step: Callable[[str], Awaitable[None]],
) -> AsyncIterator[str]:
    """Yield token deltas for the final user-facing reply only."""
    scratch_lines: list[str] = []
    steps = 0
    deadline = time.monotonic() + LOOP_TIMEOUT_S

    while steps < MAX_STEPS:
        if time.monotonic() > deadline:
            logger.warning("ReAct decision phase exceeded %ss", LOOP_TIMEOUT_S)
            break

        scratch = "\n".join(scratch_lines) if scratch_lines else ""
        decision = await _decide(
            client,
            system=full_system,
            user_message=user_message,
            scratchpad=scratch,
        )
        action = str(decision.get("action") or "").strip()

        if action in ("", "final_answer"):
            break

        if action not in _THINKING_LABELS:
            scratch_lines.append(f"invalid_tool:{action}")
            break

        await on_thinking_step(_THINKING_LABELS[action])

        args = decision.get("args")
        args_obj: dict[str, Any] = args if isinstance(args, dict) else {}
        thought = str(decision.get("thought") or "")
        try:
            result = await run_tool(action, json.dumps(args_obj))
        except Exception as exc:
            result = f"Tool error: {exc}"
            logger.exception("tool %s failed", action)

        scratch_lines.append(f"{thought}\n{action} → {result[:2000]}")
        steps += 1

        # Action tools are one-shot — stop immediately so we don't repeat them.
        if action in _TERMINAL_TOOLS:
            break

    scratch_text = "\n".join(scratch_lines) if scratch_lines else "(none)"
    system_block = (
        full_system
        + "\n\nInternal tool notes (distill for the user; never read aloud verbatim):\n"
        + scratch_text
    )

    final_messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_block},
        *history_messages,
        {"role": "user", "content": user_message},
    ]

    async for delta in client.stream_chat_messages(
        final_messages, temperature=0.65, max_tokens=512
    ):
        yield delta
