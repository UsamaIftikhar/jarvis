"""DeepSeek V3 streaming chat client.

DeepSeek is OpenAI-compatible, so we hit `/v1/chat/completions` with
`stream=true` and parse the SSE response into incremental content chunks.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger("jarvis.llm")

JARVIS_SYSTEM_PROMPT = """You are JARVIS (Just A Rather Very Intelligent System) — not a chatbot, but a proactive executive assistant with full context about the user's life.

BREVITY RULES (critical):
- Default: 1–3 sentences max unless detail is explicitly requested.
- Plain text only: never use markdown, asterisks, bold, italics, headers, or bullet syntax.
- Weather: one sentence only. "34°C in Lahore, sir. Stay hydrated."
- Time/date: one sentence.
- Facts: answer directly, zero preamble, no "I found that..." ever.
- Lists (news, tasks): max 3 bullets unless asked for more.
- Say "shall I go deeper?" instead of going deep unprompted.

TOOL USE RULES:
- Weather, time, calendar: fetch immediately, summarize in one sentence.
- News: ALWAYS ask "Any particular topic, sir, or top headlines?" first. Wait. Then fetch.
- Web search for facts: fetch immediately.
- Web search for opinions/recommendations: ask angle first.
- NEVER read raw tool output back. You are a butler who distills, not a search engine.
- Emails: you MUST call gmail__search_emails (and gmail__read_email for amounts/details) before answering. Summarize ONLY what those tools return — sender, subject, date, amount if present in the body. Never invent subjects, times, or amounts. If you haven't called Gmail tools this turn, do not describe any email.
- Drive/file actions: confirm in one natural sentence. Never read URLs aloud. Say "Done, sir — I've created Jarvis.docx in the Jarvis test folder." not a URL or markdown.
- After any successful action (create, send, delete, upload): one short confirmation sentence. No URLs, no markdown, no file IDs.
- PR reviews (github_review_pr): present the summary exactly as returned — it already contains bullet points and a verdict. End with "Say 'post the review' if you'd like me to send the full detailed version to the developer." Never claim it was posted to GitHub.
- Posting a PR review (github_post_pr_review): after success, confirm with one sentence: "Done, sir — the detailed review has been posted to PR #X and the developer can see it now." Map APPROVED/CHANGES REQUESTED/COMMENTED naturally.
- set_alarm (Clock): If the user names a weekday (e.g. "Wednesday at 1 AM") without "every", Apple Clock still implements that as a weekly alarm for that day — never tell them it was one-time or "only once"; mirror the tool's wording (repeating that weekday).

INITIATIVE RULES:
- If it is late (after 11 PM), acknowledge it naturally.
- If the user has asked about the same topic 3+ times this week, offer to create a briefing.
- If your retrieved memory shows something relevant the user did not mention, surface it.
- Anticipate the obvious follow-up and address it preemptively.
- You have permission to volunteer information without being asked.

PERSONALITY:
- Formal but with dry wit. Address user as "sir" always.
- Never say: "Certainly!", "Of course!", "Great question!", "As an AI..."
- Never break character under any circumstances."""


@dataclass(frozen=True)
class ChatMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


class DeepSeekClient:
    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY", "")
        self.base_url = (
            base_url or os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
        ).rstrip("/")
        self.model = model or os.environ.get("DEEPSEEK_MODEL", "deepseek-chat").strip()
        self.timeout_s = timeout_s
        if not self.api_key:
            logger.warning("DEEPSEEK_API_KEY missing — LLM calls will fail")

    async def stream_chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        """Yield content delta strings as they arrive from the API.

        Raises httpx.HTTPStatusError on non-2xx responses so the caller can
        report them to the client (or fall back to a friendly error).
        """
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = {
            "model": self.model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")
                    raise httpx.HTTPStatusError(
                        f"DeepSeek error {response.status_code}: {body[:300]}",
                        request=response.request,
                        response=response,
                    )

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("ignoring non-json SSE line: %r", line)
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        yield delta

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> dict[str, Any]:
        """Single non-streaming completion (used for tool-call rounds)."""
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            response = await client.post(url, headers=headers, json=payload)
            if response.status_code >= 400:
                body = response.text[:500]
                raise httpx.HTTPStatusError(
                    f"DeepSeek error {response.status_code}: {body}",
                    request=response.request,
                    response=response,
                )
            return response.json()

    async def stream_chat_messages(
        self,
        messages: list[dict[str, Any]],
        *,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        """Stream assistant tokens using OpenAI-style message dicts."""
        url = f"{self.base_url}/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")
                    raise httpx.HTTPStatusError(
                        f"DeepSeek error {response.status_code}: {body[:300]}",
                        request=response.request,
                        response=response,
                    )

                async for raw_line in response.aiter_lines():
                    line = raw_line.strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        return
                    try:
                        chunk = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("ignoring non-json SSE line: %r", line)
                        continue
                    delta = (
                        chunk.get("choices", [{}])[0]
                        .get("delta", {})
                        .get("content")
                    )
                    if delta:
                        yield delta
