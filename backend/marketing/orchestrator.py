"""Marketing Orchestrator — JARVIS routes marketing requests here.

Classifies intent → dispatches to the right specialist agent.
"""
from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from llm import DeepSeekClient

from .agents.content_agent   import build_content_agent
from .agents.social_agent    import build_social_agent
from .agents.analytics_agent import build_analytics_agent
from .agents.strategy_agent  import build_strategy_agent

logger = logging.getLogger("jarvis.marketing.orchestrator")

_ROUTER_SYSTEM = """You are a router for Khas Bazaar's marketing AI system.

Given a user message, respond with ONLY a JSON object:
{"domain": "<domain>", "intent": "<short intent>"}

Domain options:
- "content"   → generating captions, hooks, Reel briefs, content calendars, story sequences, hashtags
- "social"    → posting to Instagram/Facebook, generating images/videos via AI
- "analytics" → checking insights, performance, follower growth, post metrics
- "strategy"  → weekly review, what to post next, growth advice, improvement suggestions, product catalog management
- "general"   → anything else about the brand, products, pricing, general questions

Examples:
"write a caption for the gold vase" → {"domain": "content", "intent": "generate_caption"}
"post this to Instagram" → {"domain": "social", "intent": "post_photo"}
"generate a reel for the ribbed set" → {"domain": "social", "intent": "generate_reel"}
"generate a lifestyle image for the dusty rose vase" → {"domain": "social", "intent": "generate_image"}
"generate a product photo for the gold set" → {"domain": "social", "intent": "generate_image"}
"make a flatlay for the bunny tail stems" → {"domain": "social", "intent": "generate_image"}
"create an ai image of the vase" → {"domain": "social", "intent": "generate_image"}
"how did last week perform?" → {"domain": "analytics", "intent": "weekly_report"}
"what should I post this week?" → {"domain": "strategy", "intent": "content_plan"}
"add a new product" → {"domain": "strategy", "intent": "add_product"}
"7-day content calendar" → {"domain": "content", "intent": "content_calendar"}
"""


async def _classify_intent(client: DeepSeekClient, user_message: str) -> dict[str, str]:
    try:
        data = await client.complete(
            messages=[
                {"role": "system", "content": _ROUTER_SYSTEM},
                {"role": "user",   "content": user_message},
            ],
            tools=None,
            temperature=0.1,
            max_tokens=60,
        )
        import json, re
        content = (data.get("choices", [{}])[0].get("message") or {}).get("content", "").strip()
        m = re.search(r"\{[\s\S]*\}", content)
        if m:
            return json.loads(m.group(0))
    except Exception:
        logger.warning("Intent classification failed, defaulting to content")
    return {"domain": "content", "intent": "general"}


async def run_marketing_agent(
    *,
    client: DeepSeekClient,
    user_message: str,
    history_messages: list[dict[str, Any]],
    on_thinking_step: Callable[[str], Awaitable[None]],
) -> AsyncIterator[str]:
    """Entry point: classify → dispatch → stream final answer."""
    await on_thinking_step("Routing to Khas Bazaar marketing agent…")

    classification = await _classify_intent(client, user_message)
    domain = classification.get("domain", "content")
    intent = classification.get("intent", "")
    logger.info("Marketing intent: domain=%r intent=%r", domain, intent)

    agent_builders = {
        "content":   build_content_agent,
        "social":    build_social_agent,
        "analytics": build_analytics_agent,
        "strategy":  build_strategy_agent,
        "general":   build_content_agent,
    }

    build_fn = agent_builders.get(domain, build_content_agent)
    agent = build_fn()

    await on_thinking_step(f"Khas Bazaar {domain} agent working…")

    async for delta in agent.run(
        client=client,
        user_message=user_message,
        history_messages=history_messages,
        on_thinking_step=on_thinking_step,
    ):
        yield delta
