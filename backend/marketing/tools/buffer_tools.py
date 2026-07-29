"""Buffer GraphQL API — post to the Khas Bazaar Instagram channel.

Buffer's 2026 API is a single GraphQL endpoint (``https://api.buffer.com``)
authenticated with a Bearer API key (generate one in Buffer account settings).

Flow for "post it on insta":
  1. Resolve the Instagram channel id (cached; auto-discovered via the
     ``channels`` query, or pinned with ``BUFFER_IG_CHANNEL_ID``).
  2. Ensure we have a public image URL — if given a local file, upload it via
     ``image_host.upload_image_public`` (Buffer cannot read local files).
  3. ``createPost`` with ``mode: shareNow`` to publish immediately.

Required env:
    BUFFER_ACCESS_TOKEN   — Buffer API key (Bearer)
Optional env:
    BUFFER_IG_CHANNEL_ID  — pin the Instagram channel id (skips auto-discovery)
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .. import state
from .image_host import upload_image_public
from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.buffer")

_API_URL = "https://api.buffer.com"

# Cached Instagram channel id for this process.
_ig_channel_cache: str = ""


def _token() -> str:
    return os.environ.get("BUFFER_ACCESS_TOKEN", "").strip()


def _configured() -> bool:
    return bool(_token())


async def _graphql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_token()}",
        "Content-Type": "application/json",
    }
    payload = {"query": query, "variables": variables or {}}
    async with httpx.AsyncClient(timeout=45) as client:
        resp = await client.post(_API_URL, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    if data.get("errors"):
        msgs = "; ".join(e.get("message", str(e)) for e in data["errors"])
        raise RuntimeError(f"Buffer API error: {msgs}")
    return data.get("data", {})


async def _resolve_ig_channel() -> tuple[str, str]:
    """Return (channel_id, channel_name) for the Instagram channel.

    Honours BUFFER_IG_CHANNEL_ID; otherwise scans channels for the first
    Instagram one (preferring a name containing 'khasbazaar').
    """
    global _ig_channel_cache

    pinned = os.environ.get("BUFFER_IG_CHANNEL_ID", "").strip()
    if pinned:
        return pinned, "(pinned)"
    if _ig_channel_cache:
        return _ig_channel_cache, "(cached)"

    data = await _graphql("query { account { organizations { id } } }")
    orgs = (data.get("account") or {}).get("organizations") or []
    channels: list[dict[str, Any]] = []
    for org in orgs:
        org_id = org.get("id")
        if not org_id:
            continue
        ch_data = await _graphql(
            """
            query Ch($orgId: OrganizationId!) {
              channels(input: {organizationId: $orgId}) {
                id name service type isLocked isDisconnected
              }
            }
            """,
            {"orgId": org_id},
        )
        channels.extend(ch_data.get("channels") or [])

    insta = [
        c for c in channels
        if "instagram" in str(c.get("service", "")).lower()
        and not c.get("isDisconnected")
        and not c.get("isLocked")
    ]
    if not insta:
        raise RuntimeError(
            "No connected Instagram channel found in Buffer. "
            "Connect the khasbazaar Instagram in Buffer, or set BUFFER_IG_CHANNEL_ID."
        )
    chosen = next(
        (c for c in insta if "khasbazaar" in str(c.get("name", "")).lower().replace(" ", "")),
        insta[0],
    )
    _ig_channel_cache = chosen["id"]
    return chosen["id"], str(chosen.get("name", ""))


async def _buffer_list_channels(args: dict[str, Any]) -> str:
    if not _configured():
        return "Buffer not configured. Set BUFFER_ACCESS_TOKEN in .env."
    try:
        data = await _graphql(
            """
            query { account { organizations { id name } } }
            """
        )
        orgs = (data.get("account") or {}).get("organizations") or []
        lines: list[str] = []
        for org in orgs:
            org_id = org.get("id")
            ch_data = await _graphql(
                """
                query Ch($orgId: OrganizationId!) {
                  channels(input: {organizationId: $orgId}) {
                    id name service type isLocked isDisconnected
                  }
                }
                """,
                {"orgId": org_id},
            )
            for c in ch_data.get("channels") or []:
                flags = []
                if c.get("isLocked"):
                    flags.append("locked")
                if c.get("isDisconnected"):
                    flags.append("disconnected")
                suffix = f" [{', '.join(flags)}]" if flags else ""
                lines.append(
                    f"• {c.get('service')}/{c.get('type')} — {c.get('name')} "
                    f"— id:{c.get('id')}{suffix}"
                )
        return "Buffer channels:\n" + "\n".join(lines) if lines else "No Buffer channels found."
    except Exception as exc:
        logger.exception("buffer_list_channels failed")
        return f"Buffer channels error: {exc}"


async def _buffer_post_photo(args: dict[str, Any]) -> str:
    if not _configured():
        return "Buffer not configured. Set BUFFER_ACCESS_TOKEN in .env."

    caption = str(args.get("caption", "") or "").strip()
    image_url = str(args.get("image_url", "") or "").strip()
    image_path = str(args.get("image_path", "") or "").strip()

    # Fall back to the last generated image/caption from this session.
    if not image_url and not image_path:
        image_path = state.get_last_image()
    if not caption:
        caption = state.get_last_caption()

    if not image_url and not image_path:
        return (
            "No image to post. Generate an image first (e.g. 'generate an "
            "Instagram image for the gold rim ribbed set'), or pass image_path."
        )

    # Always post with an engaging caption + hashtags. If none was generated
    # earlier this session, create one now for the image's product.
    if not caption:
        try:
            from .content_tools import generate_post_caption
            product_id = state.get_last_image_product()
            caption = await generate_post_caption(product_id)
            if caption:
                state.set_last_caption(caption, product_id)
                logger.info("Auto-generated caption for post (%d chars)", len(caption))
        except Exception as exc:
            logger.warning("auto caption generation failed: %s", exc)

    try:
        if not image_url:
            image_url = await upload_image_public(image_path)
    except Exception as exc:
        logger.exception("image hosting failed")
        return f"Could not host the image for posting: {exc}"

    try:
        channel_id, channel_name = await _resolve_ig_channel()
    except Exception as exc:
        return f"Instagram channel error: {exc}"

    variables = {
        "input": {
            "channelId": channel_id,
            "text": caption,
            "assets": [{"image": {"url": image_url}}],
            "schedulingType": "automatic",
            "mode": "shareNow",
            "metadata": {
                "instagram": {
                    "type": "post",
                    "shouldShareToFeed": True,
                }
            },
        }
    }
    mutation = """
        mutation Create($input: CreatePostInput!) {
          createPost(input: $input) {
            __typename
            ... on PostActionSuccess { post { id status } }
            ... on MutationError { message }
          }
        }
    """
    try:
        data = await _graphql(mutation, variables)
        result = data.get("createPost") or {}
        if result.get("__typename") == "PostActionSuccess":
            post = result.get("post") or {}
            logger.info(
                "Buffer post success: channel=%s post_id=%s status=%s",
                channel_name,
                post.get("id"),
                post.get("status"),
            )
            product_id = state.get_last_image_product()
            from .catalog_tools import product_display_name

            name = product_display_name(product_id)
            if name:
                return (
                    f"The {name} image has been posted to Instagram — "
                    "you can check it there."
                )
            return "Your image has been posted to Instagram — you can check it there."
        msg = result.get("message") or "unknown error"
        return f"Buffer rejected the post: {msg}"
    except Exception as exc:
        logger.exception("buffer_post_photo failed")
        return f"Buffer post error: {exc}"


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "buffer_post_photo",
            "description": (
                "Publish a photo to the Khas Bazaar Instagram via Buffer (shareNow). "
                "Use for 'post it on insta', 'post this to Instagram', 'publish the image'. "
                "If image_path/image_url and caption are omitted, the last generated "
                "image and caption from this session are used automatically."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "caption":    {"type": "string", "description": "Caption incl. hashtags. Omit to reuse the last generated caption."},
                    "image_path": {"type": "string", "description": "Local image path. Omit to reuse the last generated image."},
                    "image_url":  {"type": "string", "description": "Public image URL (skips hosting). Optional."},
                },
                "required": [],
            },
        },
    },
    handler=_buffer_post_photo,
    thinking_label="Posting to Instagram via Buffer…",
    terminal=True,
    help_hint="publishes the generated image + caption to Khas Bazaar Instagram via Buffer",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "buffer_list_channels",
            "description": "List the social channels connected to Buffer (id, service, name). Use to find the Instagram channel id.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_buffer_list_channels,
    thinking_label="Listing Buffer channels…",
    terminal=True,
    help_hint="lists connected Buffer channels and their ids",
))
