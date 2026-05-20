"""Vertex AI tools — Imagen 3 image generation + Veo 2 video generation.

Required env vars:
    GOOGLE_CLOUD_PROJECT      — GCP project ID
    GOOGLE_CLOUD_LOCATION     — e.g. us-central1
    GOOGLE_APPLICATION_CREDENTIALS — path to service account JSON

Install: pip install google-cloud-aiplatform Pillow
"""
from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any

from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.vertex")

_OUTPUT_DIR = Path(__file__).parent.parent.parent / "generated_content"
_OUTPUT_DIR.mkdir(exist_ok=True)

_BACKEND_DIR = Path(__file__).parent.parent.parent


def _gcp_configured() -> bool:
    return bool(
        os.environ.get("GOOGLE_CLOUD_PROJECT")
        and os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    )


def _init_vertex() -> None:
    from google.cloud import aiplatform
    project  = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    aiplatform.init(project=project, location=location)


_BRAND_STYLE = (
    "Professional product photography for Khas Bazaar, a Pakistani home decor brand. "
    "Aesthetic: boho-chic, modern minimalist. Color palette: nude, beige, white, gold accents. "
    "Soft natural lighting, clean composition, Instagram-worthy."
)


# ---------------------------------------------------------------------------
# Imagen 3 — image generation (text-to-image OR product image editing)
# ---------------------------------------------------------------------------

async def _generate_image(args: dict[str, Any]) -> str:
    prompt       = str(args.get("prompt", "") or "").strip()
    product_id   = str(args.get("product_id", "") or "").strip()
    aspect_ratio = str(args.get("aspect_ratio", "1:1") or "1:1").strip()
    style        = str(args.get("style", "") or "").strip()
    num_images   = min(int(args.get("num_images", 1) or 1), 4)

    if not prompt and not product_id:
        return "Provide a prompt or product_id."
    if not _gcp_configured():
        return (
            "Google Cloud not configured. Set GOOGLE_CLOUD_PROJECT and "
            "GOOGLE_APPLICATION_CREDENTIALS in .env."
        )

    full_prompt = f"{_BRAND_STYLE} {prompt}"
    if style:
        full_prompt += f" Style: {style}."

    # Try to find a reference product image — exact ID then fuzzy name match
    product_image_path: str | None = None
    if product_id:
        from .catalog_tools import _load_catalog
        try:
            catalog = _load_catalog()
            products = catalog["products"]
            product = next((p for p in products if p["id"] == product_id), None)
            if not product:
                needle = product_id.lower().replace("-", " ").replace("_", " ")
                product = next(
                    (p for p in products
                     if needle in p.get("name", "").lower()
                     or p["id"].replace("-", " ") in needle),
                    None,
                )
            if product and product.get("image_path"):
                candidate = _BACKEND_DIR / product["image_path"]
                if candidate.exists():
                    product_image_path = str(candidate)
                    logger.info("Using reference image for %s: %s", product["id"], product_image_path)
        except Exception as exc:
            logger.warning("Could not load product image: %s", exc)

    try:
        if product_image_path:
            return await _gemini_edit_product_image(
                product_image_path=product_image_path,
                scene_prompt=full_prompt,
                num_images=num_images,
            )
        else:
            return await _gemini_generate_image(
                prompt=full_prompt,
                num_images=num_images,
            )
    except Exception as exc:
        logger.exception("generate_image failed")
        return f"Image generation error: {exc}"


async def _gemini_edit_product_image(
    product_image_path: str,
    scene_prompt: str,
    num_images: int,
) -> str:
    """Use Gemini image generation to edit product image — pass actual photo as reference."""
    import base64
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "GEMINI_API_KEY not set in .env."

    client = genai.Client(api_key=api_key)

    with open(product_image_path, "rb") as f:
        image_bytes = f.read()

    mime_type = "image/jpeg" if product_image_path.lower().endswith((".jpg", ".jpeg")) else "image/png"

    edit_instruction = (
        f"This is a product photo for Khas Bazaar, a Pakistani home decor brand. "
        f"Keep this EXACT product — same shape, same color, same texture, same details. "
        f"Replace ONLY the background and setting with: {scene_prompt}. "
        f"Make it look like a professional Instagram product photography shoot. "
        f"Boho-chic aesthetic, soft natural lighting, clean composition."
    )

    saved_paths: list[str] = []
    for _ in range(num_images):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=[
                    types.Part.from_bytes(data=image_bytes, mime_type=mime_type),
                    types.Part.from_text(text=edit_instruction),
                ],
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    img_data = part.inline_data.data
                    if isinstance(img_data, str):
                        img_bytes = base64.b64decode(img_data)
                    else:
                        img_bytes = img_data
                    filename = f"kb_gemini_{uuid.uuid4().hex[:8]}.png"
                    filepath = _OUTPUT_DIR / filename
                    filepath.write_bytes(img_bytes)
                    saved_paths.append(str(filepath))
                    logger.info("Gemini image saved: %s", filepath)
                    break
        except Exception as exc:
            logger.exception("Gemini edit failed for one image: %s", exc)

    if saved_paths:
        return _format_result(saved_paths)
    return "Gemini image generation failed — check logs for details."


async def _gemini_generate_image(prompt: str, num_images: int) -> str:
    """Text-to-image via Gemini (no product reference)."""
    import base64
    from google import genai
    from google.genai import types

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        return "GEMINI_API_KEY not set in .env."

    client = genai.Client(api_key=api_key)
    saved_paths: list[str] = []

    for _ in range(num_images):
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-image",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE", "TEXT"],
                ),
            )
            for part in response.candidates[0].content.parts:
                if part.inline_data is not None:
                    img_data = part.inline_data.data
                    img_bytes = base64.b64decode(img_data) if isinstance(img_data, str) else img_data
                    filename = f"kb_gemini_{uuid.uuid4().hex[:8]}.png"
                    filepath = _OUTPUT_DIR / filename
                    filepath.write_bytes(img_bytes)
                    saved_paths.append(str(filepath))
                    logger.info("Gemini text-to-image saved: %s", filepath)
                    break
        except Exception as exc:
            logger.exception("Gemini text-to-image failed: %s", exc)

    if saved_paths:
        return _format_result(saved_paths)
    return "Gemini image generation failed — check logs."


def _save_images(images: list, prefix: str) -> list[str]:
    saved: list[str] = []
    for image in images:
        filename = f"{prefix}_{uuid.uuid4().hex[:8]}.png"
        filepath = _OUTPUT_DIR / filename
        image.save(str(filepath))
        saved.append(str(filepath))
        logger.info("Saved: %s", filepath)
    return saved


def _format_result(paths: list[str], note: str = "") -> str:
    note_str = f" {note}" if note else ""
    return (
        f"Generated {len(paths)} image(s){note_str}:\n"
        + "\n".join(f"• {p}" for p in paths)
        + "\nSaved to generated_content/. Upload to a public URL before posting to Instagram."
    )


# ---------------------------------------------------------------------------
# Veo 2 — video generation
# ---------------------------------------------------------------------------

async def _generate_video(args: dict[str, Any]) -> str:
    prompt        = str(args.get("prompt", "") or "").strip()
    product_id    = str(args.get("product_id", "") or "").strip()
    duration_secs = min(int(args.get("duration_seconds", 8) or 8), 30)
    aspect_ratio  = str(args.get("aspect_ratio", "9:16") or "9:16").strip()

    if not prompt and not product_id:
        return "Provide a prompt or product_id."
    if not _gcp_configured():
        return "Google Cloud not configured. Set GOOGLE_CLOUD_PROJECT and GOOGLE_APPLICATION_CREDENTIALS in .env."

    # If product_id given, enrich prompt with product details
    if product_id and not prompt:
        from .catalog_tools import _load_catalog
        try:
            catalog = _load_catalog()
            product = next((p for p in catalog["products"] if p["id"] == product_id), None)
            if product:
                prompt = f"{product['name']} home decor product showcase"
        except Exception:
            pass

    brand_style = (
        "Aesthetic Pakistani home decor brand Khas Bazaar product video. "
        "Cinematic, warm, boho-chic aesthetic. Soft natural lighting. "
        "Slow smooth camera movement. Instagram Reels style."
    )
    full_prompt = f"{brand_style} {prompt}"

    try:
        _init_vertex()
        from vertexai.preview.vision_models import VideoGenerationModel

        model = VideoGenerationModel.from_pretrained("veo-2.0-generate-001")
        response = model.generate_video(
            prompt=full_prompt,
            duration_seconds=duration_secs,
            aspect_ratio=aspect_ratio,
        )

        filename = f"kb_reel_{uuid.uuid4().hex[:8]}.mp4"
        filepath = _OUTPUT_DIR / filename
        response.video.save(str(filepath))
        logger.info("Veo 2 saved: %s", filepath)

        return (
            f"Video generated ({duration_secs}s, {aspect_ratio}):\n"
            f"• {filepath}\n"
            "Saved to generated_content/. Upload to a public URL before posting as a Reel."
        )
    except ImportError:
        return "google-cloud-aiplatform not installed. Run: pip install google-cloud-aiplatform"
    except Exception as exc:
        logger.exception("generate_video failed")
        return f"Veo 2 error: {exc}"


# ---------------------------------------------------------------------------
# Helper: build Imagen prompt from product data
# ---------------------------------------------------------------------------

async def _build_product_image_prompt(args: dict[str, Any]) -> str:
    product_id = str(args.get("product_id", "") or "").strip()
    scene      = str(args.get("scene", "hero") or "hero").strip()

    from .catalog_tools import _load_catalog
    try:
        catalog = _load_catalog()
        product = next((p for p in catalog["products"] if p["id"] == product_id), None)
        if not product:
            return f"Product '{product_id}' not found."

        has_image = bool(product.get("image_path"))
        colors = ", ".join(product.get("colors", []))

        scene_prompts = {
            "hero":      f"Clean neutral background, soft studio lighting, minimal props. Premium product photography of {product['name']}.",
            "lifestyle": f"{product['name']} styled in a beautiful Pakistani home interior. Natural light from a window. Linen textures nearby. Aesthetic boho-chic setting.",
            "flatlay":   f"Flat lay of {product['name']} with complementary props: dried flowers, linen cloth, small ceramic pieces. Top-down view. Neutral background.",
            "gifting":   f"{product['name']} presented as a gift. Ribbon, tissue paper, dried petals. Warm cozy mood.",
        }
        base_prompt = scene_prompts.get(scene, scene_prompts["hero"])

        mode_note = (
            "Will use your actual product photo as reference (image editing mode) — exact product preserved."
            if has_image else
            "No product photo on file — will generate from text description."
        )

        return (
            f"IMAGEN 3 PROMPT for {product['name']} ({scene} shot):\n\n"
            f"{base_prompt}\n\n"
            f"Mode: {mode_note}\n\n"
            f"Call generate_image with product_id='{product_id}' and this prompt to create the visual."
        )
    except Exception as exc:
        return f"Prompt generation error: {exc}"


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_image",
            "description": (
                "Generate a product/lifestyle image using Vertex AI Imagen 3. "
                "ALWAYS pass product_id when generating for a specific product — "
                "this is the only way the model knows what the real product looks like. "
                "Without product_id the model generates a random wrong product. "
                "Use for 'generate a photo', 'create an image', 'make a flatlay', 'lifestyle shot'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":       {"type": "string",  "description": "Scene/background description"},
                    "product_id":   {"type": "string",  "description": "Product ID — enables image editing mode with actual product photo"},
                    "aspect_ratio": {"type": "string",  "description": "1:1 (feed), 4:5 (portrait), 9:16 (stories/reels) — default: 1:1"},
                    "style":        {"type": "string",  "description": "Extra style note e.g. 'golden hour', 'flat lay', 'ASMR close-up'"},
                    "num_images":   {"type": "integer", "description": "Number of variations (1-4, default: 1)"},
                },
                "required": ["prompt"],
            },
        },
    },
    handler=_generate_image,
    thinking_label="Generating image with Imagen 3…",
    terminal=True,
    help_hint="generates product images via Vertex AI Imagen 3 — uses your actual product photo when product_id given",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_video",
            "description": (
                "Generate a short Reel/video clip using Vertex AI Veo 2. "
                "Defaults to 9:16 aspect ratio (Instagram Reels). "
                "Use for 'make a reel', 'generate a video for this product', 'create a veo video'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":           {"type": "string",  "description": "Video description prompt"},
                    "product_id":       {"type": "string",  "description": "Product ID for context"},
                    "duration_seconds": {"type": "integer", "description": "Video length in seconds (5-30, default: 8)"},
                    "aspect_ratio":     {"type": "string",  "description": "9:16 (Reels) or 1:1 (feed) — default: 9:16"},
                },
                "required": ["prompt"],
            },
        },
    },
    handler=_generate_video,
    thinking_label="Generating video with Veo 2…",
    terminal=True,
    help_hint="generates Reel videos via Vertex AI Veo 2",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "build_product_image_prompt",
            "description": "Build an optimized Imagen 3 prompt for a specific product and scene type.",
            "parameters": {
                "type": "object",
                "properties": {
                    "product_id": {"type": "string", "description": "Product ID"},
                    "scene":      {"type": "string", "description": "Scene: hero / lifestyle / flatlay / gifting (default: hero)"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_build_product_image_prompt,
    thinking_label="Building image generation prompt…",
    terminal=False,
    help_hint="returns an optimized Imagen/Veo prompt for a product scene",
))
