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
from tools.registry import REGISTRY

logger = logging.getLogger("jarvis.agent")

MAX_STEPS = 6
LOOP_TIMEOUT_S = 30.0

# All three are read live from REGISTRY so MCP tools registered after import are included.
def _tool_help() -> str:
    return REGISTRY.tool_help()

def _thinking_label(action: str) -> str:
    return REGISTRY.thinking_labels().get(action, f"{action}…")

def _is_terminal(action: str) -> bool:
    return action in REGISTRY.terminal_tools()


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
    prompt = f"""{_tool_help()}

IMPORTANT: Every tool listed above is LIVE and already authenticated — gmail tools have OAuth access, calendar tools are connected, etc. Never assume a tool is unavailable or needs setup. If the user's request matches a tool, you MUST call it.

Return ONE JSON object only (no markdown):
{{"thought": string, "action": string, "args": object|null}}

Rules:
- If the user's request can be fulfilled by a tool, you MUST use that tool. Do NOT answer from memory.
- Only use "final_answer" when no tool applies OR after you already have tool results in the scratchpad.
- Never say you lack access, credentials, or settings — the tools handle that.

User message:
{user_message}

Scratchpad:
{scratchpad or "(empty)"}
"""
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": prompt},
    ]
    data = await client.complete(messages, tools=None, temperature=0.3, max_tokens=1500)
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
    called_actions: set[str] = set()
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
        logger.info("ReAct step %d: action=%r thought=%r", steps, action, str(decision.get("thought") or "")[:120])

        if action in ("", "final_answer"):
            break

        if action not in REGISTRY.known_names():
            logger.warning("ReAct: unknown tool %r — aborting loop", action)
            scratch_lines.append(f"invalid_tool:{action}")
            break

        if action in called_actions:
            logger.warning("ReAct: tool %r already called this turn — aborting loop", action)
            break

        await on_thinking_step(_thinking_label(action))

        args = decision.get("args")
        args_obj: dict[str, Any] = args if isinstance(args, dict) else {}
        thought = str(decision.get("thought") or "")
        try:
            result = await run_tool(action, json.dumps(args_obj))
            logger.info("ReAct tool %r result (first 200): %s", action, result[:200])
        except Exception as exc:
            result = f"Tool error: {exc}"
            logger.exception("tool %s failed", action)

        called_actions.add(action)
        scratch_lines.append(f"{thought}\n{action} → {result[:2000]}")
        steps += 1

        if _is_terminal(action):
            break

    scratch_text = "\n".join(scratch_lines) if scratch_lines else "(none)"

    # Detect whether any write action actually succeeded
    write_keywords = ("created", "uploaded", "deleted", "sent", "replied", "moved", "updated", "marked")
    error_keywords = ("error", "Error", "failed", "unavailable", "Tool error")
    write_ran = any(kw in scratch_text for kw in write_keywords)
    error_ran = any(kw in scratch_text for kw in error_keywords)
    no_action_ran = not scratch_lines

    if no_action_ran or (not write_ran and not error_ran):
        honesty_note = (
            "\n\nCRITICAL: No write operation was actually executed this turn. "
            "Do NOT claim any file, folder, email, or object was created/sent/deleted. "
            "Tell the user you were unable to complete the action and what you tried."
        )
    elif error_ran:
        honesty_note = (
            "\n\nCRITICAL: A tool returned an error above. The action DID NOT succeed. "
            "Tell the user it failed and what went wrong. NEVER claim success when a tool returned an error."
        )
    else:
        honesty_note = ""

    system_block = (
        full_system
        + "\n\nInternal tool notes (distill for the user; never read aloud verbatim):\n"
        + scratch_text
        + honesty_note
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
