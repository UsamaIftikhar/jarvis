"""Marketing Orchestrator — JARVIS routes marketing requests here.

Classifies intent → dispatches to the right specialist agent.
"""
from __future__ import annotations

import logging
import re
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

from llm import DeepSeekClient

from .agents.content_agent   import build_content_agent
from .agents.social_agent    import build_social_agent
from .agents.analytics_agent import build_analytics_agent
from .agents.strategy_agent  import build_strategy_agent
from .tools.registry import MARKETING_REGISTRY

# Ensure marketing tools are registered before shortcut lookup.
import marketing.tools.buffer_tools       # noqa: F401
import marketing.tools.gemini_playwright  # noqa: F401

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
"post it on insta" → {"domain": "social", "intent": "post_photo"}
"publish the image to instagram" → {"domain": "social", "intent": "post_photo"}
"generate a reel for the ribbed set" → {"domain": "social", "intent": "generate_reel"}
"generate a lifestyle image for the dusty rose vase" → {"domain": "social", "intent": "generate_image"}
"generate a product photo for the gold set" → {"domain": "social", "intent": "generate_image"}
"make a flatlay for the bunny tail stems" → {"domain": "social", "intent": "generate_image"}
"create an ai image of the vase" → {"domain": "social", "intent": "generate_image"}
"generate a new image for the ribbed set and post it on Instagram" → {"domain": "social", "intent": "generate_and_post"}
"make a new photo of the same product and post on insta" → {"domain": "social", "intent": "generate_and_post"}
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


_POST_PHOTO_INTENTS = frozenset({"post_photo", "post", "publish_photo", "publish"})
_GENERATE_AND_POST_INTENTS = frozenset({"generate_and_post", "create_and_post"})
_GENERATE_WORDS = ("generate", "create", "make", "another", "fresh")
_NEW_WORDS = ("new", "another", "fresh", "again")
_IMAGE_WORDS = ("image", "photo", "picture", "visual", "post")
_MEMORY_PRODUCT_PHRASES = (
    "last product", "recent product", "previous product",
    "same product", "same item", "that product",
)
_TOKEN_RE = re.compile(r"[a-z0-9]+")
_PRODUCT_STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "on", "it", "to", "for", "of", "in", "at",
    "new", "post", "generate", "create", "make", "yes", "please", "hey", "jarvis",
    "instagram", "insta", "last", "recent", "previous", "product", "same", "that",
    "this", "can", "you", "my", "me", "sir", "image", "photo", "picture",
})


def _tokens(text: str) -> set[str]:
    return set(_TOKEN_RE.findall(text.lower())) - _PRODUCT_STOPWORDS


def _token_overlap(a: set[str], b: set[str]) -> int:
    score = 0
    for ta in a:
        for tb in b:
            if ta == tb or ta in tb or tb in ta:
                score += 1
                break
    return score


def _best_catalog_match(text: str) -> str:
    from .tools.catalog_tools import _load_catalog

    msg_tokens = _tokens(text)
    if not msg_tokens:
        return ""
    best_id = ""
    best_score = 0
    try:
        for product in _load_catalog().get("products", []):
            pid = product.get("id", "")
            fields = [
                pid.replace("-", " "),
                product.get("name", ""),
                " ".join(product.get("style_tags", [])),
            ]
            for field in fields:
                score = _token_overlap(msg_tokens, _tokens(field))
                if score > best_score and score >= 2:
                    best_score = score
                    best_id = pid
    except Exception:
        pass
    return best_id


def _guess_product_from_message(
    user_message: str,
    history_messages: list[dict[str, Any]] | None = None,
) -> str:
    """Best-effort product_id from user text, session memory, or recent chat."""
    from . import state

    lower = user_message.lower()

    if any(p in lower for p in _MEMORY_PRODUCT_PHRASES):
        remembered = state.get_last_image_product()
        if remembered:
            return remembered
        if history_messages:
            for msg in reversed(history_messages):
                if msg.get("role") != "user":
                    continue
                hist_match = _best_catalog_match(str(msg.get("content", "") or ""))
                if hist_match:
                    return hist_match
        return ""

    matched = _best_catalog_match(user_message)
    if matched:
        return matched

    if history_messages:
        for msg in reversed(history_messages):
            if msg.get("role") != "user":
                continue
            content = str(msg.get("content", "") or "")
            cl = content.lower()
            if any(p in cl for p in _MEMORY_PRODUCT_PHRASES):
                remembered = state.get_last_image_product()
                if remembered:
                    return remembered
            hist_match = _best_catalog_match(content)
            if hist_match:
                return hist_match

    return state.get_last_image_product()


def _wants_generate_and_post(intent: str, user_message: str) -> bool:
    """User wants a NEW image created and then published to Instagram."""
    if intent in _GENERATE_AND_POST_INTENTS:
        return True
    lower = user_message.lower()
    if "instagram" not in lower and "insta" not in lower:
        return False
    if not any(a in lower for a in ("post", "publish", "upload", "share")):
        return False
    wants_new = any(w in lower for w in _NEW_WORDS)
    wants_generate = any(w in lower for w in _GENERATE_WORDS)
    wants_image = any(w in lower for w in _IMAGE_WORDS)
    return (wants_generate or wants_new) and wants_image


def _wants_instagram_post(domain: str, intent: str, user_message: str) -> bool:
    """True when the user is asking to publish an existing photo to Instagram."""
    if _wants_generate_and_post(intent, user_message):
        return False
    if domain != "social":
        return False
    if intent in _POST_PHOTO_INTENTS:
        return True
    lower = user_message.lower()
    if "instagram" not in lower and "insta" not in lower:
        return False
    return any(kw in lower for kw in ("post", "publish", "upload", "share"))


async def _run_post_photo_shortcut(
    user_message: str,
    on_thinking_step: Callable[[str], Awaitable[None]],
) -> str | None:
    """Call buffer_post_photo directly — don't rely on the LLM to pick the tool."""
    entry = MARKETING_REGISTRY.get("buffer_post_photo")
    if not entry:
        return None
    await on_thinking_step(entry.thinking_label)
    return await entry.handler({})


async def _run_generate_and_post_shortcut(
    user_message: str,
    on_thinking_step: Callable[[str], Awaitable[None]],
    *,
    history_messages: list[dict[str, Any]] | None = None,
    product_id: str | None = None,
) -> str | None:
    """Generate a fresh Gemini image, then publish it to Instagram via Buffer."""
    from . import state

    gen_entry = MARKETING_REGISTRY.get("generate_image_gemini")
    post_entry = MARKETING_REGISTRY.get("buffer_post_photo")
    if not gen_entry or not post_entry:
        return None

    if not product_id:
        product_id = _guess_product_from_message(user_message, history_messages)
    if not product_id:
        state.set_pending_action(state.PENDING_GENERATE_AND_POST)
        return (
            "I need to know which product to shoot. Try: "
            "'generate a new Instagram image for the gold rim ribbed set and post it'."
        )

    state.clear_pending_action()
    state.clear_last_caption()
    state.set_last_image_product(product_id)

    gen_label = MARKETING_REGISTRY.thinking_labels().get(
        "generate_image_gemini", "Generating image…"
    )
    await on_thinking_step(gen_label)
    gen_result = await gen_entry.handler({"product_id": product_id})
    if "Generated" not in gen_result and "generated_content/" not in gen_result:
        return gen_result

    await on_thinking_step(post_entry.thinking_label)
    post_result = await post_entry.handler({})
    if post_result.startswith(("Buffer rejected", "Buffer post error", "Could not host", "No image", "Instagram channel", "Buffer not configured")):
        return post_result
    return post_result


async def run_marketing_agent(
    *,
    client: DeepSeekClient,
    user_message: str,
    history_messages: list[dict[str, Any]],
    on_thinking_step: Callable[[str], Awaitable[None]],
) -> AsyncIterator[str]:
    """Entry point: classify → dispatch → stream final answer."""
    from . import state

    await on_thinking_step("Routing to Khas Bazaar marketing agent…")

    # Follow-up after "which product?" — must run real tools, not the main LLM.
    if state.get_pending_action() == state.PENDING_GENERATE_AND_POST:
        product_id = _guess_product_from_message(user_message, history_messages)
        if product_id:
            result = await _run_generate_and_post_shortcut(
                user_message,
                on_thinking_step,
                history_messages=history_messages,
                product_id=product_id,
            )
            if result is not None:
                yield result
                return
        yield (
            "I still need the product name, sir — for example gold rim ribbed set "
            "or dusty rose vase."
        )
        return

    classification = await _classify_intent(client, user_message)
    domain = classification.get("domain", "content")
    intent = classification.get("intent", "")
    logger.info("Marketing intent: domain=%r intent=%r", domain, intent)

    if _wants_generate_and_post(intent, user_message):
        result = await _run_generate_and_post_shortcut(
            user_message,
            on_thinking_step,
            history_messages=history_messages,
        )
        if result is not None:
            yield result
            return

    if _wants_instagram_post(domain, intent, user_message):
        result = await _run_post_photo_shortcut(user_message, on_thinking_step)
        if result is not None:
            yield result
            return

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
