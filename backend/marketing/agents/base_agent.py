"""BaseMarketingAgent — reusable ReAct loop for all specialist marketing agents.

Each specialist agent (ContentAgent, SocialAgent, etc.) instantiates this with
its own registry subset and system prompt addendum.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from llm import DeepSeekClient
from marketing.tools.registry import MarketingRegistry

logger = logging.getLogger("jarvis.marketing.agent")

MAX_STEPS = 8
LOOP_TIMEOUT_S = 45.0


def _parse_json(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[-1]
        raw = raw.rsplit("```", 1)[0].strip()
    m = re.search(r"\{[\s\S]*\}", raw)
    if m:
        raw = m.group(0)
    return json.loads(raw)


class BaseMarketingAgent:
    """
    Reusable ReAct loop. Pass a filtered registry so each specialist only
    sees tools relevant to its domain.
    """

    def __init__(
        self,
        registry: MarketingRegistry,
        system_prompt: str,
        max_steps: int = MAX_STEPS,
    ) -> None:
        self.registry = registry
        self.system_prompt = system_prompt
        self.max_steps = max_steps

    async def _decide(
        self,
        client: DeepSeekClient,
        user_message: str,
        scratchpad: str,
    ) -> dict[str, Any]:
        already_called = [
            line.split(" →")[0].strip().split("\n")[-1]
            for line in scratchpad.splitlines()
            if " →" in line
        ] if scratchpad else []
        blocked_note = (
            f"\nTools already called this turn (DO NOT call again): {already_called}"
            if already_called else ""
        )

        prompt = f"""{self.registry.tool_help()}

IMPORTANT: Every tool listed is LIVE. If the user's request matches a tool, call it.

Return ONE JSON object only (no markdown):
{{"thought": string, "action": string, "args": object|null}}

Rules:
- Use a tool if one applies. Do NOT answer from memory alone.
- Only use "final_answer" when no tool applies OR after you have tool results.
- CRITICAL: If the user asks you to DO something (post, create, generate, save, add, send) you MUST call a tool first. Never confirm an action without calling it — that is a lie.
- Each tool can only be called ONCE per turn.{blocked_note}
- If scratchpad already has the answer, use final_answer immediately.

User message:
{user_message}

Scratchpad:
{scratchpad or "(empty)"}
"""
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user",   "content": prompt},
        ]
        data = await client.complete(messages, tools=None, temperature=0.3, max_tokens=1500)
        msg = data.get("choices", [{}])[0].get("message") or {}
        content = (msg.get("content") or "").strip()
        if not content:
            return {"thought": "", "action": "final_answer", "args": None}
        try:
            return _parse_json(content)
        except Exception:
            logger.warning("base_agent parse failed: %r", content[:240])
            return {"thought": "parse error", "action": "final_answer", "args": None}

    async def run(
        self,
        *,
        client: DeepSeekClient,
        user_message: str,
        history_messages: list[dict[str, Any]],
        on_thinking_step: Callable[[str], Awaitable[None]],
    ) -> AsyncIterator[str]:
        """Yield token deltas for the final user-facing reply."""
        scratch_lines: list[str] = []
        called_actions: set[str] = set()
        steps = 0
        deadline = time.monotonic() + LOOP_TIMEOUT_S

        entry = None
        result = ""
        while steps < self.max_steps:
            if time.monotonic() > deadline:
                logger.warning("Marketing agent loop timeout after %ss", LOOP_TIMEOUT_S)
                break

            scratch = "\n".join(scratch_lines) if scratch_lines else ""
            decision = await self._decide(client, user_message, scratch)
            action = str(decision.get("action") or "").strip()
            logger.info("Marketing step %d: action=%r", steps, action)

            if action in ("", "final_answer"):
                break

            if action not in self.registry.known_names():
                logger.warning("Marketing agent: unknown tool %r", action)
                scratch_lines.append(f"invalid_tool:{action}")
                break

            if action in called_actions:
                logger.warning("Marketing agent: tool %r already called — stopping", action)
                scratch_lines.append(f"SYSTEM: {action} blocked (already called).")
                break

            labels = self.registry.thinking_labels()
            await on_thinking_step(labels.get(action, f"{action}…"))

            args_raw = decision.get("args")
            args_obj: dict[str, Any] = args_raw if isinstance(args_raw, dict) else {}
            thought = str(decision.get("thought") or "")

            try:
                entry = self.registry.get(action)
                result = await entry.handler(args_obj)  # type: ignore[union-attr]
                logger.info("Marketing tool %r result: %s", action, result[:200])
            except Exception as exc:
                result = f"Tool error: {exc}"
                logger.exception("Marketing tool %s failed", action)

            called_actions.add(action)
            scratch_lines.append(f"{thought}\n{action} → {result[:3000]}")
            steps += 1

            if self.registry.terminal_tools() and action in self.registry.terminal_tools():
                # Passthrough: yield tool result directly, skip final LLM reformat
                if entry and entry.passthrough:
                    yield result
                    return
                break

        scratch_text = "\n".join(scratch_lines) if scratch_lines else "(none)"

        result_text = "\n".join(
            line.split(" → ", 1)[1] if " → " in line else ""
            for line in scratch_lines
        )

        _SUCCESS = ("created", "added", "posted", "sent", "saved", "updated", "generated", "scheduled", "published", "via buffer")
        _ERRORS  = ("error", "Error", "failed", "unavailable", "Tool error", "blocked")

        had_error = any(kw in result_text for kw in _ERRORS)
        write_succeeded = any(kw in result_text for kw in _SUCCESS)
        user_wants_action = any(
            kw in user_message.lower()
            for kw in ("post", "send", "create", "generate", "add", "save", "schedule",
                       "write", "make", "publish", "upload")
        )
        no_tool_ran = not scratch_lines

        if user_wants_action and no_tool_ran:
            yield "I'm sorry, sir — I wasn't able to execute that action. Please try again."
            return

        if user_wants_action and not write_succeeded and scratch_lines and had_error:
            yield "I'm afraid that action failed. "
            yield "There was an error — please check the backend logs for details."
            return

        if had_error and not write_succeeded:
            honesty_note = (
                "\n\nFACT: The tool returned an error. The action DID NOT complete. "
                "Say so clearly and briefly."
            )
            final_temp = 0.2
        else:
            honesty_note = ""
            final_temp = 0.65

        system_block = (
            self.system_prompt
            + "\n\nTool results:\n"
            + scratch_text
            + honesty_note
            + "\n\nOUTPUT RULES:"
            + "\n- If the tool result contains captions, Reel briefs, calendars, or any generated content: reproduce it EXACTLY as returned. No labels. No bold. No reformatting. No 'Here is the caption:' intro. Just the content."
            + "\n- If the tool result is data/insights: summarize naturally in 2-3 sentences."
            + "\n- Never add structural labels like 'Line 1:', 'Hook:', 'CTA:', 'Hashtags:' — these are internal instructions, never visible to the user."
        )

        final_messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_block},
            *history_messages,
            {"role": "user", "content": user_message},
        ]

        async for delta in client.stream_chat_messages(
            final_messages, temperature=final_temp, max_tokens=600
        ):
            yield delta
