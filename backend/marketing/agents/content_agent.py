"""ContentAgent — generates captions, Reel briefs, content calendars, story sequences."""
from __future__ import annotations

from marketing.tools.catalog_tools import _load_catalog  # noqa: PLC2701
from marketing.tools.content_tools import KHAS_BAZAAR_CONTENT_SYSTEM  # noqa: PLC2701
from marketing.tools.registry import MARKETING_REGISTRY

from .base_agent import BaseMarketingAgent

# Import tool modules to trigger their MARKETING_REGISTRY.register() calls
import marketing.tools.catalog_tools  # noqa: F401
import marketing.tools.content_tools  # noqa: F401

_CONTENT_AGENT_SYSTEM = KHAS_BAZAAR_CONTENT_SYSTEM + """

YOUR ROLE: You are the ContentAgent for Khas Bazaar.
You specialise in generating captions, Reel briefs, content calendars, and story sequences.

TOOL USE RULES:
- generate_caption already fetches product data internally — call it directly with product_id. Do NOT call get_product before generate_caption.
- generate_reel_brief already fetches product data internally — call it directly. Do NOT call get_product first.
- Only call list_products if the user has NOT specified a product and you need to find the right product_id.
- Never call get_product as a lookup step before a generate_ tool — it's redundant and wastes time.

OUTPUT RULES:
- When a generate_ tool returns captions or content: output it EXACTLY as returned. No rephrasing, no labels, no bold, no intro sentence.
- The generated caption IS the final answer. Don't summarize it. Don't describe it. Just output it.
"""


def build_content_agent() -> BaseMarketingAgent:
    return BaseMarketingAgent(
        registry=MARKETING_REGISTRY,
        system_prompt=_CONTENT_AGENT_SYSTEM,
        max_steps=6,
    )
