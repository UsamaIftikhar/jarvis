"""Lightweight cross-turn memory for the marketing agents.

Stores the most recently generated image path and caption so a later turn
like "post it on insta" can resolve what to post without re-specifying it.

Backed by a small JSON file under ``generated_content/`` so it survives
process reloads (uvicorn --reload) within a session.
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.marketing.state")

_STATE_PATH = Path(__file__).parent.parent / "generated_content" / ".post_state.json"

PENDING_GENERATE_AND_POST = "generate_and_post"


def _read() -> dict[str, Any]:
    try:
        if _STATE_PATH.is_file():
            return json.loads(_STATE_PATH.read_text())
    except Exception as exc:
        logger.warning("post_state read failed: %s", exc)
    return {}


def _write(data: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(data, indent=2))
    except Exception as exc:
        logger.warning("post_state write failed: %s", exc)


def set_last_image(path: str, product_id: str = "") -> None:
    data = _read()
    data["last_image_path"] = path
    data["last_image_product"] = product_id
    data["last_image_at"] = time.time()
    _write(data)
    logger.info("Recorded last generated image: %s", path)


def get_last_image() -> str:
    path = str(_read().get("last_image_path", "") or "")
    if path and Path(path).is_file():
        return path
    return find_latest_generated_image()


def set_last_image_product(product_id: str) -> None:
    product_id = (product_id or "").strip()
    if not product_id:
        return
    data = _read()
    data["last_image_product"] = product_id
    _write(data)


def get_last_image_product() -> str:
    return str(_read().get("last_image_product", "") or "")


def find_latest_generated_image() -> str:
    """Newest kb_* image in generated_content/ (fallback after server reload)."""
    gen_dir = _STATE_PATH.parent
    if not gen_dir.is_dir():
        return ""
    candidates = sorted(
        gen_dir.glob("kb_*.png"),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(candidates[0]) if candidates else ""


def set_last_caption(text: str, product_id: str = "") -> None:
    text = (text or "").strip()
    if not text:
        return
    data = _read()
    data["last_caption"] = text
    data["last_caption_product"] = product_id
    data["last_caption_at"] = time.time()
    _write(data)
    logger.info("Recorded last caption (%d chars)", len(text))


def clear_last_caption() -> None:
    data = _read()
    data.pop("last_caption", None)
    data.pop("last_caption_at", None)
    _write(data)


def get_last_caption() -> str:
    return str(_read().get("last_caption", "") or "")


def set_pending_action(action: str) -> None:
    data = _read()
    data["pending_action"] = action
    data["pending_at"] = time.time()
    _write(data)
    logger.info("Pending marketing action set: %s", action)


def get_pending_action() -> str:
    return str(_read().get("pending_action", "") or "")


def clear_pending_action() -> None:
    data = _read()
    if "pending_action" in data:
        data.pop("pending_action", None)
        data.pop("pending_at", None)
        _write(data)
