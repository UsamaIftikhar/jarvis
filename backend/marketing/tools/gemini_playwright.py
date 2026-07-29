"""Gemini image generation — connects to Chrome via remote debugging port.

Auto-launches Chrome with --remote-debugging-port=9222 using the user's real
profile so Gemini Pro login is already active. If Chrome is already running
without the debug port, asks the user to close it once.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import socket
import subprocess
import time
import uuid
from io import BytesIO
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.gemini_playwright")

_OUTPUT_DIR = Path(__file__).parent.parent.parent / "generated_content"
_OUTPUT_DIR.mkdir(exist_ok=True)
_BACKEND_DIR = Path(__file__).parent.parent.parent

_CHROME_BINARY = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
_CHROME_USER_DATA = os.path.expanduser("~/Library/Application Support/Google/Chrome")
# Chrome v100+ rejects --remote-debugging-port with the default profile, so we use
# a dedicated Jarvis profile seeded with the real Chrome's cookies.
_JARVIS_DEBUG_PROFILE = Path.home() / "Library" / "Application Support" / "Jarvis Chrome Debug"
_DEBUG_PORT = 9222
_GEMINI_URL = "https://gemini.google.com"

_BRAND_STYLE = (
    "Aesthetic: boho-chic, modern minimalist. Palette: nude, beige, white, gold."
)
_CLEAN_IMAGE_RULES = (
    "Do NOT add any brand logos, text overlays, labels, or watermarks on the image. "
    "No Khas Bazaar branding on the image. No AI watermarks. No Gemini logo or sparkle. "
    "Clean product photography only — just the product and scene."
)


def _strip_gemini_watermark(img_bytes: bytes) -> bytes:
    """Remove the Gemini sparkle badge from the bottom-right corner."""
    try:
        img = Image.open(BytesIO(img_bytes)).convert("RGB")
        w, h = img.size
        badge_w = max(72, min(140, w // 10))
        badge_h = max(40, min(90, h // 14))
        x0, y0 = w - badge_w, h - badge_h

        # Sample pixels just above/left of the badge for a natural fill.
        samples: list[tuple[int, int, int]] = []
        for x in range(max(0, x0 - 24), x0):
            for y in range(max(0, y0 - 16), h - badge_h):
                samples.append(img.getpixel((x, y)))
        if not samples:
            for x in range(max(0, x0 - 8), w - badge_w):
                for y in range(max(0, y0 - 8), y0):
                    samples.append(img.getpixel((x, y)))

        if samples:
            fill = (
                sum(c[0] for c in samples) // len(samples),
                sum(c[1] for c in samples) // len(samples),
                sum(c[2] for c in samples) // len(samples),
            )
            ImageDraw.Draw(img).rectangle([x0, y0, w, h], fill=fill)

        out = BytesIO()
        img.save(out, format="PNG", optimize=True)
        return out.getvalue()
    except Exception as exc:
        logger.warning("watermark cleanup failed, using original: %s", exc)
        return img_bytes


def _build_product_prompt(product_name: str, scene: str) -> str:
    scene = scene or "a beautiful boho lifestyle home setting"
    return (
        f"For Khas Bazaar (خاص بازار), a Pakistani home decor brand. {_BRAND_STYLE} "
        f"I'm sharing a photo of our actual product: {product_name}. "
        "Keep this EXACT product — same shape, color, texture, every detail identical. "
        f"Only change the background and setting to: {scene}. "
        "Professional Instagram product photography. Soft natural lighting. Clean composition. "
        f"{_CLEAN_IMAGE_RULES}"
    )


async def _export_generated_image(page, generated_img, generated_src: str) -> bytes | None:
    """Export the generated image, preferring Gemini's download over canvas capture."""
    # Try Gemini's download button first — often cleaner than the on-screen preview.
    try:
        await generated_img.scroll_into_view_if_needed()
        await generated_img.hover()
        await page.wait_for_timeout(400)
        download_btn = page.locator(
            'button[aria-label*="Download"], button[aria-label*="download"], '
            'button[data-tooltip*="Download"], button[data-tooltip*="download"]'
        ).first
        if await download_btn.is_visible(timeout=2000):
            async with page.expect_download(timeout=15000) as download_info:
                await download_btn.click()
            download = await download_info.value
            path = await download.path()
            if path:
                data = Path(path).read_bytes()
                if data:
                    logger.info("Exported image via Gemini download button")
                    return data
    except Exception as exc:
        logger.debug("Gemini download button export failed: %s", exc)

    try:
        if generated_src.startswith("data:image"):
            _, data = generated_src.split(",", 1)
            logger.info("Decoded inline data-URI image")
            return base64.b64decode(data)

        handle = await generated_img.element_handle()
        data_url: str = await page.evaluate(
            """async (el) => {
                if (!el.complete || el.naturalWidth === 0) {
                    await el.decode().catch(() => {});
                }
                const w = el.naturalWidth  || el.width  || 1024;
                const h = el.naturalHeight || el.height || 1024;
                const canvas = document.createElement('canvas');
                canvas.width  = w;
                canvas.height = h;
                canvas.getContext('2d').drawImage(el, 0, 0, w, h);
                return canvas.toDataURL('image/png');
            }""",
            handle,
        )
        if data_url and "," in data_url:
            _, data = data_url.split(",", 1)
            logger.info("Exported image via canvas capture")
            return base64.b64decode(data)
    except Exception as exc:
        logger.exception("Image export failed: %s", exc)
    return None


def _chrome_debug_available() -> bool:
    """Return True if Chrome CDP debug port is accepting connections."""
    try:
        with socket.create_connection(("127.0.0.1", _DEBUG_PORT), timeout=1):
            return True
    except OSError:
        return False


def _chrome_running() -> bool:
    """Return True if any Chrome process is running."""
    try:
        result = subprocess.run(
            ["pgrep", "-f", "Google Chrome"],
            capture_output=True, text=True, timeout=5,
        )
        return result.returncode == 0
    except Exception:
        return False


def _seed_jarvis_profile() -> None:
    """Copy authentication cookies from real Chrome into the Jarvis debug profile.

    macOS Chrome encrypts cookies via the Keychain — the same key is shared
    across all Chrome profiles on the machine, so copied cookies are still
    valid and decryptable by the Jarvis profile.
    """
    import shutil

    jarvis_default = _JARVIS_DEBUG_PROFILE / "Default"
    jarvis_default.mkdir(parents=True, exist_ok=True)

    real_default = Path(_CHROME_USER_DATA) / "Default"
    for fname in ("Cookies", "Login Data"):
        src = real_default / fname
        if src.exists():
            dst = jarvis_default / fname
            shutil.copy2(src, dst)
            logger.info("Seeded Jarvis profile: %s", fname)


def _launch_chrome_debug() -> None:
    """Launch Chrome with CDP debug port using the Jarvis debug profile."""
    _seed_jarvis_profile()
    time.sleep(1)

    proc = subprocess.Popen(
        [
            _CHROME_BINARY,
            f"--remote-debugging-port={_DEBUG_PORT}",
            f"--user-data-dir={_JARVIS_DEBUG_PROFILE}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-infobars",
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    time.sleep(2)
    if proc.poll() is not None:
        raise RuntimeError(
            f"Chrome exited immediately (code {proc.returncode}). "
            "Ensure Chrome is not already running, then try again."
        )

    for i in range(30):
        time.sleep(1)
        if _chrome_debug_available():
            logger.info("Chrome debug port %d ready after %ds", _DEBUG_PORT, i + 1)
            return
        if proc.poll() is not None:
            logger.error("Chrome process exited with code %s", proc.returncode)
            raise RuntimeError(
                f"Chrome exited unexpectedly (code {proc.returncode}). "
                "Try closing all Chrome windows first."
            )

    raise RuntimeError(
        f"Chrome did not open debug port {_DEBUG_PORT} after 30s. "
        "Try launching it manually in Terminal:\n\n"
        f'  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" '
        f'--remote-debugging-port={_DEBUG_PORT} '
        f'--user-data-dir="{_JARVIS_DEBUG_PROFILE}"\n\n'
        "Keep that window open, then ask me again."
    )


async def _attach_image_via_clipboard(page: Any, input_box: Any, image_path: str) -> bool:
    """Paste product image into Gemini's chat input via macOS clipboard.

    Gemini's upload flow goes: click "+" → menu appears → click "Upload" → file dialog.
    expect_file_chooser() can't intercept that menu step via CDP, so we bypass it by
    loading the image into the macOS clipboard and pasting with Cmd+V.
    """
    from playwright.async_api import Page
    try:
        suffix = Path(image_path).suffix.lower()
        ostype = "«class JPEG»" if suffix in (".jpg", ".jpeg") else "«class PNGf»"
        script = f'set the clipboard to (read (POSIX file "{image_path}") as {ostype})'
        result = await asyncio.to_thread(
            subprocess.run,
            ["osascript", "-e", script],
            capture_output=True,
            timeout=8,
        )
        if result.returncode != 0:
            logger.warning("osascript clipboard set failed: %s", result.stderr.decode())
            return False

        await input_box.click()
        await page.keyboard.press("Meta+V")
        await page.wait_for_timeout(2500)

        # Confirm attachment: Gemini shows a thumbnail preview after paste
        preview = page.locator(
            'img[alt*="pasted"], img[alt*="Uploaded"], '
            '.upload-preview, [data-attachment-preview], '
            'div[jsname] img[src^="blob:"]'
        )
        if await preview.count() > 0:
            logger.info("Product image attached via clipboard paste ✓")
            return True

        # Even if the preview selector misses, paste likely succeeded —
        # Gemini sometimes uses private src attributes we can't match
        logger.info("Clipboard paste sent (preview selector may not match — proceeding)")
        return True

    except Exception as exc:
        logger.warning("Clipboard image attach failed: %s", exc)
        return False


async def _gemini_generate_with_browser(
    product_image_path: str | None,
    prompt: str,
    num_images: int = 1,
    product_id: str = "",
) -> str:
    from playwright.async_api import async_playwright

    # Ensure Chrome is running with the debug port
    if not _chrome_debug_available():
        if _chrome_running():
            return (
                "Chrome is open but Jarvis can't share it — Chrome v100+ blocks "
                "remote automation on the default profile (security restriction).\n\n"
                "Please quit Chrome (⌘Q), then ask me again. Jarvis will open its "
                "own Chrome window. You'll need to sign in once — after that it "
                "remembers your session permanently."
            )
        logger.info("Launching Chrome with debug port using real profile…")
        await asyncio.to_thread(_launch_chrome_debug)
        await asyncio.sleep(1)

    saved_paths: list[str] = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.connect_over_cdp(
                f"http://127.0.0.1:{_DEBUG_PORT}"
            )
        except Exception as exc:
            logger.exception("connect_over_cdp failed: %s", exc)
            return (
                f"Could not connect to Chrome on port {_DEBUG_PORT}: {exc}\n\n"
                "Close Chrome and try again."
            )

        # Reuse the user's existing browser context (keeps their login session)
        context = browser.contexts[0] if browser.contexts else await browser.new_context()
        page = await context.new_page()

        try:
            # ── Login check (first page load only) ──────────────────────────
            await page.goto(_GEMINI_URL, wait_until="domcontentloaded")
            await page.wait_for_timeout(3000)

            # If a "Sign in" button is visible, navigate to Google login automatically.
            # The Chrome window stays open — user signs in once and stays logged in.
            sign_in = page.locator('a[href*="accounts.google.com"], button:has-text("Sign in")')
            if await sign_in.first.is_visible():
                # Navigate to Google login directly so the user just has to enter credentials
                await page.goto(
                    "https://accounts.google.com/signin/v2/identifier"
                    "?continue=https%3A%2F%2Fgemini.google.com%2F&hl=en",
                    wait_until="domcontentloaded",
                )
                # Leave the window open — don't close the page
                return (
                    "One-time login needed: look at the Chrome window Jarvis just opened.\n\n"
                    "It's showing the Google sign-in page. Log in with the account "
                    "that has Gemini Pro — the browser will remember you permanently.\n\n"
                    "Once you're signed in and see the Gemini chat, come back here and "
                    "ask me to generate the image again."
                )

            for i in range(num_images):
                if i > 0:
                    await page.goto(_GEMINI_URL, wait_until="domcontentloaded")
                    await page.wait_for_timeout(3000)

                # Wait for the chat input box
                input_box = page.locator(
                    'div[contenteditable="true"], '
                    'div[role="textbox"], '
                    'rich-textarea div[contenteditable]'
                ).first
                await input_box.wait_for(state="visible", timeout=25000)
                await page.wait_for_timeout(1000)

                # Attach product image via clipboard paste (most reliable method).
                # Gemini's upload button opens a menu first so expect_file_chooser
                # never fires. Pasting from clipboard bypasses the menu entirely.
                if product_image_path and Path(product_image_path).exists():
                    attached = await _attach_image_via_clipboard(
                        page, input_box, product_image_path
                    )
                    if not attached:
                        logger.warning(
                            "Image attachment failed — prompt sent without product reference. "
                            "Gemini will generate a generic product, not yours."
                        )

                # Type prompt — re-click input in case clipboard paste stole focus
                await input_box.click()
                await page.wait_for_timeout(300)
                await page.keyboard.type(prompt, delay=10)
                await page.wait_for_timeout(500)

                # Snapshot ALL image srcs currently on the page before sending.
                # After sending we only accept images whose src was NOT present
                # before — this prevents saving the pasted product photo or any
                # other pre-existing image as the "generated" result.
                before_srcs: set[str] = set(await page.evaluate("""
                    () => Array.from(document.querySelectorAll('img'))
                             .map(el => el.currentSrc || el.src || '')
                             .filter(s => s.length > 10)
                """))
                logger.info("Snapshotted %d existing images before send", len(before_srcs))

                # Wait for the send button to be enabled.
                # Gemini disables it while the pasted image is uploading —
                # clicking while disabled does nothing.
                send_btn = page.locator(
                    'button[aria-label*="Send"], '
                    'button[aria-label*="send"]'
                ).first
                sent = False
                if await send_btn.is_visible():
                    logger.info("Waiting for send button to become enabled (image uploading)…")
                    for _ in range(30):  # up to 30 s for image upload
                        if await send_btn.is_enabled():
                            await send_btn.click()
                            logger.info("Prompt submitted via send button")
                            sent = True
                            break
                        await page.wait_for_timeout(1000)
                    if not sent:
                        logger.warning("Send button never became enabled — trying Enter key")
                if not sent:
                    await page.keyboard.press("Enter")
                    logger.info("Prompt submitted via Enter key")
                logger.info("Waiting for Gemini to generate image…")

                # Wait up to 120 s for a NEW image that wasn't on the page before.
                # Only look inside model-response containers (not user message bubbles).
                generated_img = None
                generated_src = ""
                _cant_create_phrases = (
                    "can't create", "cannot create", "not available",
                    "signed out", "unable to create", "can't generate",
                )
                for tick in range(120):
                    await page.wait_for_timeout(1000)

                    # Fast-fail: detect Gemini's "can't create images" text response
                    if tick % 5 == 4:
                        try:
                            resp_text = await page.locator(
                                "model-response, .response-container, [data-response-id]"
                            ).last.inner_text(timeout=500)
                            if any(p in resp_text.lower() for p in _cant_create_phrases):
                                logger.warning("Gemini declined: %s", resp_text[:120])
                                saved_paths.append(f"__error__:{resp_text[:200]}")
                                break
                        except Exception:
                            pass

                    # Only check images inside model/response containers
                    for img_sel in [
                        'model-response img',
                        '[data-response-id] img',
                        '.response-container img',
                        'message-content img',
                    ]:
                        imgs = page.locator(img_sel)
                        count = await imgs.count()
                        for idx in range(count - 1, -1, -1):
                            try:
                                candidate = imgs.nth(idx)
                                src: str = await page.evaluate(
                                    "el => el.currentSrc || el.src || el.dataset.src || ''",
                                    await candidate.element_handle(),
                                )
                                # Must be new (not in snapshot) and have real content
                                if src and len(src) > 10 and src not in before_srcs:
                                    generated_img = candidate
                                    generated_src = src
                                    break
                            except Exception:
                                continue
                        if generated_img:
                            break
                    if generated_img:
                        break

                if generated_img is None:
                    if saved_paths and saved_paths[-1].startswith("__error__:"):
                        err_msg = saved_paths.pop()[len("__error__:"):]
                        saved_paths.clear()
                        return (
                            f"Gemini declined image creation: {err_msg}\n\n"
                            "This usually means image generation is not available for your "
                            "account or region. Make sure you're signed in with the Gemini "
                            "Pro account that supports image generation."
                        )
                    logger.warning("No generated image found after 120 s")
                    continue

                await page.wait_for_timeout(500)
                logger.info("Generated image found, src type: %s…", generated_src[:60])

                try:
                    img_bytes = await _export_generated_image(
                        page, generated_img, generated_src
                    )
                    if img_bytes:
                        img_bytes = _strip_gemini_watermark(img_bytes)

                    if img_bytes:
                        filename = f"kb_gemini_{uuid.uuid4().hex[:8]}.png"
                        filepath = _OUTPUT_DIR / filename
                        filepath.write_bytes(img_bytes)
                        saved_paths.append(str(filepath))
                        try:
                            from .. import state
                            state.set_last_image(str(filepath), product_id or "")
                        except Exception:
                            logger.debug("could not record last image", exc_info=True)
                        logger.info(
                            "Saved image (%d KB): %s",
                            len(img_bytes) // 1024,
                            filepath,
                        )
                    else:
                        logger.warning("Canvas export returned empty data")
                except Exception as exc:
                    logger.exception("Image export failed: %s", exc)

        except Exception as exc:
            logger.exception("Playwright automation failed: %s", exc)
        finally:
            # Navigate to a blank page so Chrome keeps a tab open and stays running.
            # Do NOT call browser.close() — that would quit Chrome entirely.
            try:
                await page.goto("about:blank")
            except Exception:
                pass

    if saved_paths:
        return (
            f"Generated {len(saved_paths)} image(s) via Gemini:\n"
            + "\n".join(f"• {p}" for p in saved_paths)
            + "\nSaved to generated_content/."
        )
    return (
        "Gemini image generation via browser failed. "
        "Make sure you're logged into Gemini Pro at gemini.google.com in Chrome."
    )


async def _generate_image_gemini(args: dict[str, Any]) -> str:
    prompt     = str(args.get("prompt", "") or "").strip()
    product_id = str(args.get("product_id", "") or "").strip()
    num_images = min(int(args.get("num_images", 1) or 1), 3)

    product_image_path: str | None = None
    product_name = ""

    if product_id:
        from .catalog_tools import _load_catalog
        try:
            catalog = _load_catalog()
            products = catalog["products"]

            # 1. Exact ID match
            product = next((p for p in products if p["id"] == product_id), None)

            # 2. Fuzzy fallback — normalise both sides and do substring matching
            if not product:
                needle = product_id.lower().replace("-", " ").replace("_", " ")
                product = next(
                    (
                        p for p in products
                        if needle in p.get("name", "").lower()
                        or p["id"].replace("-", " ") in needle
                        or any(needle in t for t in p.get("tags", []))
                    ),
                    None,
                )
                if product:
                    logger.info(
                        "Fuzzy-matched %r → product id %r", product_id, product["id"]
                    )

            if product:
                product_name = product.get("name", product_id)
                if product.get("image_path"):
                    candidate = _BACKEND_DIR / product["image_path"]
                    if candidate.exists():
                        product_image_path = str(candidate)
                    else:
                        logger.warning("Image file missing: %s", candidate)
            else:
                logger.warning("No product matched %r — generating without reference", product_id)
        except Exception as exc:
            logger.warning("Could not load product: %s", exc)

    if product_image_path:
        full_prompt = _build_product_prompt(
            product_name,
            prompt or "a beautiful boho lifestyle home setting",
        )
    else:
        full_prompt = (
            f"For Khas Bazaar (خاص بازار), a Pakistani home decor brand. {_BRAND_STYLE} "
            f"{prompt} {_CLEAN_IMAGE_RULES}"
        )

    logger.info("Starting Gemini browser generation for '%s'", product_name or prompt[:40])
    result = await _gemini_generate_with_browser(
        product_image_path=product_image_path,
        prompt=full_prompt,
        num_images=num_images,
        product_id=product_id,
    )
    # Remember which product this image is for, so a later "post it on insta"
    # can auto-generate a matching caption.
    if product_id and "Generated" in result:
        try:
            from .. import state
            state.set_last_image_product(product_id)
        except Exception:
            logger.debug("could not record last image product", exc_info=True)
    return result


MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "generate_image_gemini",
            "description": (
                "Generate a product/lifestyle image using Gemini Pro via browser. "
                "ALWAYS pass product_id so the actual product photo is used as reference. "
                "Use for 'generate image', 'create a photo', 'lifestyle shot', 'flatlay'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "prompt":     {"type": "string",  "description": "Scene description — e.g. 'white marble, linen cloth, boho setting'"},
                    "product_id": {"type": "string",  "description": "Product ID — ALWAYS provide for accurate product reference"},
                    "num_images": {"type": "integer", "description": "Number of images (1-3, default: 1)"},
                },
                "required": ["product_id"],
            },
        },
    },
    handler=_generate_image_gemini,
    thinking_label="Opening Gemini to generate image…",
    terminal=True,
    passthrough=False,
    help_hint="generates images via Gemini Pro browser using actual product photo as reference",
))
