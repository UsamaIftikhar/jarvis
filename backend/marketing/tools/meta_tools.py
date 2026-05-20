"""Meta Graph API tools — post to Instagram Business + Facebook Page.

Required env vars:
    META_PAGE_ID              — Facebook Page ID
    META_IG_USER_ID           — Instagram Business Account ID
    META_PAGE_ACCESS_TOKEN    — Long-lived Page Access Token
"""
from __future__ import annotations

import logging
import os
from typing import Any

import httpx

from .registry import MARKETING_REGISTRY, MarketingToolEntry

logger = logging.getLogger("jarvis.marketing.meta")

_GRAPH = "https://graph.facebook.com/v19.0"


def _token() -> str:
    return os.environ.get("META_PAGE_ACCESS_TOKEN", "")

def _page_id() -> str:
    return os.environ.get("META_PAGE_ID", "")

def _ig_user_id() -> str:
    return os.environ.get("META_IG_USER_ID", "")

def _configured() -> bool:
    return bool(_token() and _page_id() and _ig_user_id())


# ---------------------------------------------------------------------------
# Instagram — photo post (two-step: create container → publish)
# ---------------------------------------------------------------------------

async def _instagram_post_photo(args: dict[str, Any]) -> str:
    image_url = str(args.get("image_url", "") or "").strip()
    caption   = str(args.get("caption", "") or "").strip()
    if not image_url:
        return "Provide image_url (publicly accessible URL to the image)."
    if not _configured():
        return "Meta credentials not configured. Set META_PAGE_ACCESS_TOKEN, META_PAGE_ID, META_IG_USER_ID in .env."
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            # Step 1: Create media container
            r1 = await client.post(
                f"{_GRAPH}/{_ig_user_id()}/media",
                params={
                    "image_url": image_url,
                    "caption":   caption,
                    "access_token": _token(),
                },
            )
            r1.raise_for_status()
            container_id = r1.json().get("id")
            if not container_id:
                return f"Failed to create media container: {r1.text}"

            # Step 2: Publish container
            r2 = await client.post(
                f"{_GRAPH}/{_ig_user_id()}/media_publish",
                params={
                    "creation_id":  container_id,
                    "access_token": _token(),
                },
            )
            r2.raise_for_status()
            post_id = r2.json().get("id", "")
        return f"posted to Instagram — post ID: {post_id}"
    except Exception as exc:
        logger.exception("instagram_post_photo failed")
        return f"Instagram post error: {exc}"


# ---------------------------------------------------------------------------
# Instagram — Reel post
# ---------------------------------------------------------------------------

async def _instagram_post_reel(args: dict[str, Any]) -> str:
    video_url = str(args.get("video_url", "") or "").strip()
    caption   = str(args.get("caption", "") or "").strip()
    cover_url = str(args.get("cover_url", "") or "").strip()
    if not video_url:
        return "Provide video_url (publicly accessible URL to the video, mp4)."
    if not _configured():
        return "Meta credentials not configured."
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            params: dict[str, Any] = {
                "media_type":   "REELS",
                "video_url":    video_url,
                "caption":      caption,
                "access_token": _token(),
            }
            if cover_url:
                params["cover_url"] = cover_url

            r1 = await client.post(f"{_GRAPH}/{_ig_user_id()}/media", params=params)
            r1.raise_for_status()
            container_id = r1.json().get("id")
            if not container_id:
                return f"Failed to create Reel container: {r1.text}"

            r2 = await client.post(
                f"{_GRAPH}/{_ig_user_id()}/media_publish",
                params={"creation_id": container_id, "access_token": _token()},
            )
            r2.raise_for_status()
            post_id = r2.json().get("id", "")
        return f"Reel posted to Instagram — post ID: {post_id}"
    except Exception as exc:
        logger.exception("instagram_post_reel failed")
        return f"Instagram Reel error: {exc}"


# ---------------------------------------------------------------------------
# Facebook Page — photo post
# ---------------------------------------------------------------------------

async def _facebook_post_photo(args: dict[str, Any]) -> str:
    image_url = str(args.get("image_url", "") or "").strip()
    caption   = str(args.get("caption", "") or "").strip()
    if not image_url:
        return "Provide image_url."
    if not _configured():
        return "Meta credentials not configured."
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.post(
                f"{_GRAPH}/{_page_id()}/photos",
                params={
                    "url":          image_url,
                    "message":      caption,
                    "access_token": _token(),
                },
            )
            r.raise_for_status()
            post_id = r.json().get("id", "")
        return f"posted to Facebook Page — post ID: {post_id}"
    except Exception as exc:
        logger.exception("facebook_post_photo failed")
        return f"Facebook post error: {exc}"


# ---------------------------------------------------------------------------
# Get Instagram insights for a post
# ---------------------------------------------------------------------------

async def _get_post_insights(args: dict[str, Any]) -> str:
    post_id = str(args.get("post_id", "") or "").strip()
    if not post_id:
        return "Provide post_id."
    if not _configured():
        return "Meta credentials not configured."
    try:
        metrics = "impressions,reach,saved,likes,comments,shares,profile_visits"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{_GRAPH}/{post_id}/insights",
                params={
                    "metric":       metrics,
                    "access_token": _token(),
                },
            )
            r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return f"No insights available for post {post_id} yet (usually takes 24h)."
        lines = [f"Insights for post {post_id}:"]
        for m in data:
            lines.append(f"• {m['name']}: {m.get('values', [{}])[-1].get('value', 'N/A')}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("get_post_insights failed")
        return f"Insights error: {exc}"


# ---------------------------------------------------------------------------
# Get account-level Instagram insights
# ---------------------------------------------------------------------------

async def _get_account_insights(args: dict[str, Any]) -> str:
    period = str(args.get("period", "week") or "week").strip()
    if not _configured():
        return "Meta credentials not configured."
    try:
        metrics = "follower_count,impressions,reach,profile_views,website_clicks"
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                f"{_GRAPH}/{_ig_user_id()}/insights",
                params={
                    "metric":       metrics,
                    "period":       period,
                    "access_token": _token(),
                },
            )
            r.raise_for_status()
        data = r.json().get("data", [])
        if not data:
            return "No account insights available yet."
        lines = [f"Khas Bazaar Instagram insights ({period}):"]
        for m in data:
            values = m.get("values", [])
            latest = values[-1].get("value", "N/A") if values else "N/A"
            lines.append(f"• {m['name']}: {latest}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("get_account_insights failed")
        return f"Account insights error: {exc}"


# ---------------------------------------------------------------------------
# Register tools
# ---------------------------------------------------------------------------

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "instagram_post_photo",
            "description": "Post a photo to the Khas Bazaar Instagram Business account with a caption.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {"type": "string", "description": "Publicly accessible URL to the image (JPEG/PNG)"},
                    "caption":   {"type": "string", "description": "Full caption including hashtags"},
                },
                "required": ["image_url", "caption"],
            },
        },
    },
    handler=_instagram_post_photo,
    thinking_label="Posting photo to Instagram…",
    terminal=True,
    help_hint="posts photo to Khas Bazaar Instagram via Meta Graph API",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "instagram_post_reel",
            "description": "Post a Reel video to the Khas Bazaar Instagram account.",
            "parameters": {
                "type": "object",
                "properties": {
                    "video_url": {"type": "string", "description": "Publicly accessible URL to the video (mp4)"},
                    "caption":   {"type": "string", "description": "Full caption including hashtags"},
                    "cover_url": {"type": "string", "description": "Optional thumbnail image URL"},
                },
                "required": ["video_url", "caption"],
            },
        },
    },
    handler=_instagram_post_reel,
    thinking_label="Posting Reel to Instagram…",
    terminal=True,
    help_hint="posts Reel video to Khas Bazaar Instagram",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "facebook_post_photo",
            "description": "Post a photo to the Khas Bazaar Facebook Page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "image_url": {"type": "string", "description": "Publicly accessible URL to the image"},
                    "caption":   {"type": "string", "description": "Post caption"},
                },
                "required": ["image_url", "caption"],
            },
        },
    },
    handler=_facebook_post_photo,
    thinking_label="Posting to Facebook Page…",
    terminal=True,
    help_hint="posts photo to Khas Bazaar Facebook Page",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_post_insights",
            "description": "Get engagement metrics for a specific Instagram post: reach, saves, likes, comments, shares.",
            "parameters": {
                "type": "object",
                "properties": {
                    "post_id": {"type": "string", "description": "Instagram media/post ID"},
                },
                "required": ["post_id"],
            },
        },
    },
    handler=_get_post_insights,
    thinking_label="Fetching post insights…",
    terminal=True,
    help_hint="returns reach, saves, likes, comments for a specific post",
))

MARKETING_REGISTRY.register(MarketingToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_account_insights",
            "description": "Get Khas Bazaar Instagram account-level insights: follower count, reach, impressions, profile views.",
            "parameters": {
                "type": "object",
                "properties": {
                    "period": {"type": "string", "description": "day / week / month (default: week)"},
                },
                "required": [],
            },
        },
    },
    handler=_get_account_insights,
    thinking_label="Pulling account analytics…",
    terminal=True,
    help_hint="account-level Instagram analytics: followers, reach, impressions",
))
