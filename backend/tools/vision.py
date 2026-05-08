from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.tools")


async def _read_screen_gemini(args: dict[str, Any]) -> str:
    try:
        import mss
        from PIL import Image
        from google import genai
    except ImportError:
        return "mss, pillow, or google-genai is missing. Please install them."

    api_key = os.environ.get("GEMINI_API_KEY", "").strip()
    if not api_key:
        return "Screenshot vision requires GEMINI_API_KEY in the backend .env."

    prompt = str(args.get("prompt", "") or "")

    try:
        with mss.mss() as sct:
            monitor = sct.monitors[0]
            sct_img = sct.grab(monitor)
            img = Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")

        def _call_gemini() -> str:
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=[img, prompt],
            )
            return response.text or ""

        return await asyncio.to_thread(_call_gemini)
    except Exception as exc:
        logger.exception("Gemini screen read failed")
        return f"Error reading screen with Gemini: {exc}"


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "read_screen_gemini",
            "description": "Capture the screen using mss and use Gemini vision to analyze it based on a prompt. Use when the user says 'look at my screen' or 'what is this error?'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "The instruction or question for Gemini about the screen.",
                    },
                },
                "required": ["prompt"],
            },
        },
    },
    handler=_read_screen_gemini,
    thinking_label="Capturing screen…",
    help_hint="Capture screen and ask Gemini. ALWAYS call this when asked about the screen — screen changes constantly.",
))
