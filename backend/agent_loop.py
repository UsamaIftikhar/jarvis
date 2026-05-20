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
    blocked_calls: list[str],
) -> dict[str, Any]:
    blocked_note = (
        f"\nAlready called this turn (do NOT repeat with the same args): {blocked_calls}"
        if blocked_calls else ""
    )

    prompt = f"""{_tool_help()}

IMPORTANT: Every tool listed above is LIVE and already authenticated. If the user's request matches a tool, you MUST call it.

Return ONE JSON object only (no markdown):
{{"thought": string, "action": string, "args": object|null}}

Rules:
- Use a tool if one applies. Do NOT answer from memory.
- Only use "final_answer" when no tool applies OR after you already have tool results in the scratchpad.
- CRITICAL: If the user is asking you to DO something (post, send, create, review, delete, upload, submit, reply) you MUST call a tool. Never confirm an action without calling the corresponding tool first — doing so is a lie.
- Never say you lack access, credentials, or settings — the tools handle that.
- You MAY call the same tool multiple times in one turn if the arguments differ (e.g. reading two different emails is fine).
- Do NOT call the same tool with the exact same arguments more than once.{blocked_note}

MULTI-STEP LOGIC:
- If a search returned "No files found" or "not found", the NEXT action is to CREATE it — do not search again.
- To create a file inside a folder: call gdrive_upload_file with parent_folder_name="folder name" — no prior search needed, folder is auto-created.
- To create a folder then file: call gdrive_upload_file once with parent_folder_name set — it handles both.
- If the scratchpad already has the answer, use final_answer immediately.

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


def _is_param_error(result: str) -> bool:
    """Return True when a tool result is a JSON-schema / Zod parameter validation error."""
    return result.startswith("Error: [") and any(
        kw in result for kw in ('"invalid_type"', '"Required"', '"code"')
    )


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
    # Track (action, canonical_args_json) so the same tool with different args is allowed.
    called_calls: set[tuple[str, str]] = set()
    param_retries: dict[tuple[str, str], int] = {}  # one-retry budget per (action, args) on param errors
    steps = 0
    deadline = time.monotonic() + LOOP_TIMEOUT_S

    while steps < MAX_STEPS:
        if time.monotonic() > deadline:
            logger.warning("ReAct decision phase exceeded %ss", LOOP_TIMEOUT_S)
            break

        scratch = "\n".join(scratch_lines) if scratch_lines else ""
        blocked_calls = [f"{a}({cargs})" for a, cargs in called_calls]
        decision = await _decide(
            client,
            system=full_system,
            user_message=user_message,
            scratchpad=scratch,
            blocked_calls=blocked_calls,
        )
        action = str(decision.get("action") or "").strip()
        logger.info("ReAct step %d: action=%r thought=%r", steps, action, str(decision.get("thought") or "")[:120])

        if action in ("", "final_answer"):
            break

        if action not in REGISTRY.known_names():
            logger.warning("ReAct: unknown tool %r — aborting loop", action)
            scratch_lines.append(f"invalid_tool:{action}")
            break

        args = decision.get("args")
        args_obj: dict[str, Any] = args if isinstance(args, dict) else {}
        canonical_args = json.dumps(args_obj, sort_keys=True)
        call_key = (action, canonical_args)

        if call_key in called_calls:
            logger.warning("ReAct: tool %r called again with same args — aborting loop", action)
            scratch_lines.append(f"SYSTEM: {action} was blocked (duplicate call with identical args). No action was performed.")
            break

        await on_thinking_step(_thinking_label(action))

        thought = str(decision.get("thought") or "")
        try:
            result = await run_tool(action, json.dumps(args_obj))
            logger.info("ReAct tool %r result (first 200): %s", action, result[:200])
        except Exception as exc:
            result = f"Tool error: {exc}"
            logger.exception("tool %s failed", action)

        if _is_param_error(result) and param_retries.get(call_key, 0) < 1:
            param_retries[call_key] = param_retries.get(call_key, 0) + 1
            logger.info("ReAct: tool %r had param error — allowing one retry", action)
        else:
            called_calls.add(call_key)
        scratch_lines.append(f"{thought}\n{action} → {result[:2000]}")
        steps += 1

        if _is_terminal(action):
            break

    scratch_text = "\n".join(scratch_lines) if scratch_lines else "(none)"

    # Classify outcome — inspect only the tool RESULT portion (after " → "), not the LLM thought,
    # to avoid false positives when the thought mentions past errors.
    result_text = "\n".join(
        line.split(" → ", 1)[1] if " → " in line else ""
        for line in scratch_lines
    )

    _WRITE_SUCCESS = ("created", "uploaded", "deleted", "sent", "replied", "moved", "updated", "marked", "saved",
                      "posted", "Review posted", "comment on PR")
    _ERRORS        = ("error", "Error", "failed", "unavailable", "Tool error", "blocked")
    _WRITE_INTENT  = ("create", "make", "add", "upload", "save", "delete", "remove", "send",
                      "reply", "write", "put", "build", "new folder", "new file",
                      "post", "submit", "publish", "push", "deploy")

    write_succeeded  = any(kw in result_text for kw in _WRITE_SUCCESS)
    had_error        = any(kw in result_text for kw in _ERRORS)
    user_wants_write = any(kw in user_message.lower() for kw in _WRITE_INTENT)
    no_tool_ran      = not scratch_lines

    # Short-circuit: user wanted an action but no tool ran at all (LLM went straight to final_answer)
    if user_wants_write and no_tool_ran:
        yield "I'm sorry, sir — I wasn't able to execute that action. Please try again."
        return

    # Short-circuit: tool ran but errored, and write didn't succeed
    if user_wants_write and not write_succeeded and scratch_lines and had_error:
        yield "I'm afraid that action failed, sir. "
        if "blocked" in result_text:
            yield "The agent got stuck in a loop — please try rephrasing the request."
        else:
            yield "There was an error completing it — please check the backend logs for details."
        return

    # Build final system block with honest context
    if had_error and not write_succeeded:
        honesty_note = (
            "\n\nFACT: The tool above returned an error. The action DID NOT complete. "
            "State this clearly and briefly. Do not claim success."
        )
        final_temp = 0.2
    elif user_wants_write and not write_succeeded and not no_tool_ran:
        honesty_note = (
            "\n\nFACT: No write/create/delete action was executed this turn — only read/search tools ran. "
            "Do NOT claim any file, folder, email, or object was created or modified. "
            "Tell the user plainly that you were unable to complete it and suggest they try again."
        )
        final_temp = 0.2
    else:
        honesty_note = ""
        final_temp = 0.65

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
        final_messages, temperature=final_temp, max_tokens=512
    ):
        yield delta
