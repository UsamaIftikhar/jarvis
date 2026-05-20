"""Content generation tools for Khas Bazaar — captions, hooks, briefs, calendars."""
from __future__ import annotations

import json
import logging
import os
import random
from datetime import date, timedelta
from typing import Any

import httpx

from .catalog_tools import _load_catalog
from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.content")

# ---------------------------------------------------------------------------
# Khas Bazaar Content Brain — baked-in marketing expertise
# ---------------------------------------------------------------------------

KHAS_BAZAAR_CONTENT_SYSTEM = """You are the senior content strategist and copywriter for Khas Bazaar (خاص بازار), a Pakistani home decor brand selling aesthetic, boho-chic, and Nordic minimalist pieces.

BRAND IDENTITY:
- Name: Khas Bazaar (khas = special/exclusive in Urdu)
- Tagline: "Apne ghar ko khas banao"
- Products: Ceramic vases, ribbed sets with gold foil, bunny tail grass, planters, figurines
- Aesthetic: Boho-chic, modern aesthetic, Nordic minimalism
- Palette: nude, beige, dusty rose, white, gold accents, forest green
- Market: Pakistan — Lahore, Karachi, Islamabad
- Target customer: Pakistani women 20-32 who follow aesthetic home pages, want Pinterest-worthy homes
- Conversion: WhatsApp DM and Instagram DM (NOT website clicks)

LANGUAGE RULES (critical):
- Write in Hinglish: 70% English, 30% natural Urdu words woven in organically
- Never write formal/literary Urdu — only conversational spoken Urdu words
- Good Urdu words to use naturally: ghar (home), sundar (beautiful), khas (special), bilkul (absolutely), zaroor (definitely), pyaar (love), khubsoorat (beautiful), seedha (directly), koi (any/someone), dekho (look), lagta (feels/seems), pasand (like/prefer), mil (get/find)
- NEVER use: "Certainly!", "Of course!", "As an AI", corporate-speak
- Tone: Warm, aspirational, like a stylish friend sharing decor tips

HOOK FORMULAS (first line must stop the scroll):
1. Question: "Kya aapka ghar bhi aisa ho sakta hai? 🏡"
2. Transformation: "Shelf se puri room ka vibe badal gaya ✨"
3. Trend: "Ye trend Pakistani homes mein aa gaya hai 🔥"
4. Contrast: "Seedha se sundar — sirf ek piece se 😍"
5. Curiosity: "Log ye kyon itna order kar rahe hain? 👀"
6. Pain point: "Ghar sahi lagta hi nahi? Ye dekho 🏠"

CAPTION STRUCTURE:
Line 1: HOOK (stop the scroll — max 10 words)
[blank line]
2-3 lines: Product story / lifestyle connection
[blank line]
CTA: Always end with ONE of:
- "DM us to order 🤍"
- "Comment PRICE for details 💬"
- "WhatsApp link in bio 📲"
- "DM karo order ke liye ✨"

HASHTAG RULES:
Always include exactly 15 hashtags at the end:
5 broad: #homedecor #homeaesthetics #aestheticroom #interiordecor #homeinspo
4 Pakistan: #pakistanihomedecor #lahoredecor #pakistanishopping #onlineshopping
4 niche (product-specific): varies per product
2 brand: #KhasBazaar #خاص_بازار

CONTENT PILLAR ROTATION (follow this order):
1. Hero Shot (pure product beauty)
2. Lifestyle (product in a real home setting)
3. Education (how to style, tips)
4. Behind the Scenes
5. Repeat with variation

REEL/VIDEO BRIEF FORMAT:
Scene 1 (0-3s): HOOK — what the viewer sees first
Scene 2 (3-8s): Product reveal / transformation
Scene 3 (8-15s): Lifestyle context / styling
Scene 4 (15-20s): CTA overlay
Audio: [trending Pakistani/English audio suggestion]
Text overlays: [what text appears on screen]

SEASONAL AWARENESS:
- Eid ul Fitr / Eid ul Adha: Gift sets, home decor for guests
- Wedding season (Oct-Mar): Decor for walima, bridal shower gifts
- Ghar banao season (Jan-Mar): People redecorate after New Year
- Back to school/uni (Aug-Sep): Desk decor, study aesthetic

PERFORMANCE PRINCIPLES:
- Saves > Likes. Content that gets saved = purchase intent. Design for saves.
- Reels get 3-5x more reach than photos. Prioritize Reels.
- First 3 seconds of a Reel determine if it goes viral. Obsess over the hook.
- Reply to every comment in the first hour. The algorithm rewards this heavily.
- Carousel posts get 3x more swipes if slide 1 says "swipe for the secret →"
"""


import re as _re

_LABEL_RE = _re.compile(
    r"\*{1,2}(?:Line\s*\d+\s*\([^)]+\)|Hook|CTA|Hashtags?|Product\s+story|Caption\s*\d*|Body\s+copy)\s*:?\*{0,2}\s*",
    _re.IGNORECASE,
)

def _strip_labels(text: str) -> str:
    """Remove any structural labels the LLM inserts despite instructions."""
    text = _LABEL_RE.sub("", text)
    # Remove "Here's the caption:", "Got it, sir.", intro sentences
    text = _re.sub(r"^(Got it[\.,].*?\n|Here'?s?\s+(?:the|your|a)\s+caption.*?\n|Sure[\.,].*?\n)", "", text, flags=_re.IGNORECASE | _re.MULTILINE)
    # Collapse 3+ blank lines to 2
    text = _re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


async def _llm_content_call(prompt: str, max_tokens: int = 1000) -> str:
    api_key  = os.environ.get("DEEPSEEK_API_KEY", "")
    base_url = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
    model    = os.environ.get("DEEPSEEK_MODEL", "deepseek-chat")
    payload  = {
        "model": model,
        "messages": [
            {"role": "system", "content": KHAS_BAZAAR_CONTENT_SYSTEM},
            {"role": "user",   "content": prompt},
        ],
        "stream": False,
        "temperature": 0.8,
        "max_tokens": max_tokens,
    }
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base_url}/v1/chat/completions", headers=headers, json=payload)
        resp.raise_for_status()
    return (resp.json().get("choices", [{}])[0].get("message") or {}).get("content", "")


async def _generate_caption(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    pillar     = str(args.get("pillar", "lifestyle") or "lifestyle").strip()
    tone       = str(args.get("tone", "") or "").strip()
    extra      = str(args.get("extra_context", "") or "").strip()

    try:
        catalog = _load_catalog()
        product = next((p for p in catalog["products"] if p["id"] == product_id), None)
        if not product:
            return f"Product '{product_id}' not found. Use list_products first."

        hooks = product.get("scroll_stop_hooks", [])
        hook_example = random.choice(hooks) if hooks else ""
        angles = product.get("content_angles", [])
        angle_example = random.choice(angles) if angles else ""

        prompt = f"""Write 3 Instagram caption variations for Khas Bazaar.

Product: {product['name']}
Description: {product['description']}
Content Pillar: {pillar}
Hook inspiration (don't copy exactly, make it your own): {hook_example}
Content angle: {angle_example}
{f"Additional context: {extra}" if extra else ""}
{f"Tone: {tone}" if tone else ""}

RULES:
- First line = scroll-stopping hook (max 10 words, no label, no "Line 1:" prefix)
- Blank line after hook
- 2-3 lines body copy in Hinglish
- Blank line
- One CTA line
- Blank line
- 15 hashtags on one line

Output ONLY the 3 captions, ready to copy-paste into Instagram.
No structural labels. No "Caption 1:" headers. No "Hook:" prefixes. No markdown bold.
Separate each caption with a line of dashes: ---

Example of correct format:
Ye piece literally shelf ka game badal dega 😍

Gold rim ka shimmer + ribbed texture itna elegant hai ke koi bhi corner instantly premium ho jata hai.
Boho ho ya Nordic — dono mein fit hai bilkul perfect.

DM karo order ke liye ✨

#homedecor #homeaesthetics #aestheticroom #interiordecor #homeinspo #pakistanihomedecor #lahoredecor #pakistanishopping #onlineshopping #ceramicvase #goldrim #ribbedvase #bohodecor #minimalisthome #KhasBazaar
---
[caption 2]
---
[caption 3]
"""
        result = await _llm_content_call(prompt, max_tokens=1200)
        return _strip_labels(result)
    except Exception as exc:
        logger.exception("generate_caption failed")
        return f"Content generation error: {exc}"


async def _generate_reel_brief(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    concept    = str(args.get("concept", "") or "").strip()

    try:
        catalog = _load_catalog()
        product = next((p for p in catalog["products"] if p["id"] == product_id), None)
        if not product:
            return f"Product '{product_id}' not found."

        formats = product.get("best_formats", [])
        format_hint = ", ".join(formats) if formats else "lifestyle reel"

        prompt = f"""Create a complete Reel/video brief for Khas Bazaar's Instagram.

Product: {product['name']}
Description: {product['description']}
Style tags: {', '.join(product.get('style_tags', []))}
Colors: {', '.join(product.get('colors', []))}
{f"Concept: {concept}" if concept else f"Suggested format: {format_hint}"}

Write a complete production brief including:

CONCEPT TITLE: [catchy name]
HOOK (first 3 seconds): [exactly what viewer sees]
SCENE BREAKDOWN:
- Scene 1 (0-3s): ...
- Scene 2 (3-8s): ...
- Scene 3 (8-15s): ...
- Scene 4 (15-20s): ...
AUDIO SUGGESTION: [trending audio type / specific song if relevant to Pakistan]
TEXT OVERLAYS: [text that appears on screen each scene]
CAPTION: [full caption with hashtags]

Also write a GEMINI/AI IMAGE GENERATION PROMPT that would create a stunning hero image for this product.
Format it as:
IMAGEN PROMPT: [detailed prompt for Imagen 3 / Veo 2]
"""
        result = await _llm_content_call(prompt, max_tokens=1500)
        return _strip_labels(result)
    except Exception as exc:
        logger.exception("generate_reel_brief failed")
        return f"Content generation error: {exc}"


async def _generate_content_calendar(args: dict[str, Any]) -> str:
    days  = min(int(args.get("days", 7) or 7), 30)
    focus = str(args.get("focus", "") or "").strip()

    try:
        catalog = _load_catalog()
        products = [p for p in catalog["products"] if p.get("status") == "active"]
        product_names = [p["name"] for p in products]
        pillars = [p["name"] for p in catalog.get("content_pillars", [])]

        today = date.today()
        prompt = f"""Create a {days}-day Instagram content calendar for Khas Bazaar.

Available products: {', '.join(product_names)}
Content pillars to rotate: {', '.join(pillars)}
{f"Focus/theme: {focus}" if focus else ""}
Start date: {today.strftime('%A, %d %B %Y')}
Peak posting time: 8-10 PM PKT
Platform: Instagram + Facebook (same content)

For each day provide:
DATE | DAY | FORMAT (Reel/Photo/Carousel/Stories) | PRODUCT | PILLAR | HOOK (first line only) | CAPTION CTA

Present as a clean table. After the table, add:
WEEKLY STRATEGY NOTE: What's the goal this week and what to watch for.
"""
        result = await _llm_content_call(prompt, max_tokens=2000)
        return _strip_labels(result)
    except Exception as exc:
        logger.exception("generate_content_calendar failed")
        return f"Content generation error: {exc}"


async def _generate_story_sequence(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    goal       = str(args.get("goal", "engagement") or "engagement").strip()

    try:
        catalog = _load_catalog()
        product = next((p for p in catalog["products"] if p["id"] == product_id), None)
        if not product:
            return f"Product '{product_id}' not found."

        prompt = f"""Design a 5-slide Instagram Stories sequence for Khas Bazaar.

Product: {product['name']}
Description: {product['description']}
Goal: {goal} (e.g. engagement = polls/questions, sales = swipe-up/DM CTA)

For each story slide:
SLIDE [N]:
- Visual: [what to show]
- Text overlay: [exact text on screen]
- Interactive element: [poll / question sticker / swipe-up / DM button / none]
- Duration: [seconds]

End with a CTA slide driving to WhatsApp or DM.
"""
        result = await _llm_content_call(prompt, max_tokens=800)
        return _strip_labels(result)
    except Exception as exc:
        logger.exception("generate_story_sequence failed")
        return f"Content generation error: {exc}"


async def _generate_hashtag_set(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    try:
        catalog = _load_catalog()
        brand_hashtags = catalog.get("hashtag_strategy", {})
        product = next((p for p in catalog["products"] if p["id"] == product_id), None) if product_id else None

        prompt = f"""Generate an optimized hashtag set for a Khas Bazaar Instagram post.

Brand hashtag strategy:
Tier 1 (broad): {brand_hashtags.get('tier1_broad', [])}
Tier 2 (Pakistan): {brand_hashtags.get('tier2_pakistan', [])}
Tier 3 (niche): {brand_hashtags.get('tier3_niche', [])}
Tier 4 (brand): {brand_hashtags.get('tier4_brand', [])}
{f"Product: {product['name']} — Tags: {product.get('style_tags', [])}" if product else ""}

Return exactly 15 hashtags using the mix: 5 tier1 + 4 tier2 + 4 tier3 + 2 tier4.
Choose the most relevant niche hashtags for this product.
Format as a single line ready to paste into Instagram.
"""
        result = await _llm_content_call(prompt, max_tokens=200)
        return _strip_labels(result)
    except Exception as exc:
        logger.exception("generate_hashtag_set failed")
        return f"Hashtag generation error: {exc}"


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_caption",
            "description": (
                "Generate 3 Instagram caption variations for a Khas Bazaar product. "
                "Includes hook, body copy, CTA, and 15 hashtags. Writes in Hinglish. "
                "Use for 'write a caption', 'caption for this product', 'post text'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id":    {"type": "string", "description": "Product ID from catalog e.g. 'gold-rim-ribbed-set-nude'"},
                    "pillar":        {"type": "string", "description": "Content pillar: hero-shot / lifestyle / education / behind-the-scenes / social-proof (default: lifestyle)"},
                    "tone":          {"type": "string", "description": "Tone adjustment: funny / emotional / educational / urgent (optional)"},
                    "extra_context": {"type": "string", "description": "Any extra context e.g. 'Eid is coming' or 'launching tomorrow'"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_generate_caption,
    thinking_label="Writing captions for Khas Bazaar…",
    terminal=True,
    passthrough=True,
    help_hint="generates 3 Hinglish caption variations with hooks + hashtags",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_reel_brief",
            "description": (
                "Generate a complete Reel/video production brief for a product — "
                "scene breakdown, audio suggestion, text overlays, caption, and an Imagen/Veo AI image prompt. "
                "Use for 'reel idea', 'video brief', 'what should I post as a reel'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID"},
                    "concept":    {"type": "string", "description": "Optional concept direction e.g. 'transformation', 'ASMR texture', 'gifting'"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_generate_reel_brief,
    thinking_label="Creating Reel production brief…",
    terminal=True,
    passthrough=True,
    help_hint="full Reel brief: scenes, audio, overlays, caption, AI image prompt",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_content_calendar",
            "description": (
                "Generate a multi-day Instagram content calendar for Khas Bazaar. "
                "Rotates products across content pillars with hooks for each day. "
                "Use for 'content plan', 'what to post this week', '7-day calendar'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "days":  {"type": "integer", "description": "Number of days (1-30, default 7)"},
                    "focus": {"type": "string",  "description": "Optional theme e.g. 'Eid collection', 'new arrivals', 'gifting season'"},
                },
                "required": [],
            },
        },
    },
    handler=_generate_content_calendar,
    thinking_label="Building content calendar…",
    terminal=True,
    passthrough=True,
    help_hint="generates day-by-day posting plan with product rotation and hooks",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_story_sequence",
            "description": (
                "Design a 5-slide Instagram Stories sequence for a product. "
                "Includes visuals, text overlays, interactive elements, and CTA. "
                "Use for 'story ideas', 'create a story sequence', 'stories for this product'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID"},
                    "goal":       {"type": "string", "description": "Goal: engagement / sales / awareness (default: engagement)"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_generate_story_sequence,
    thinking_label="Designing story sequence…",
    terminal=True,
    passthrough=True,
    help_hint="5-slide story sequence with visuals, overlays, and interactive elements",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_hashtag_set",
            "description": "Generate an optimized 15-hashtag set for a Khas Bazaar post.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID for niche hashtag selection (optional)"},
                },
                "required": [],
            },
        },
    },
    handler=_generate_hashtag_set,
    thinking_label="Generating hashtag strategy…",
    terminal=True,
    help_hint="returns optimized 15-hashtag set for Pakistan home decor audience",
))
