"""Proactive background jobs (Layer 3) — APScheduler tasks that push HUD alerts.

All jobs are defensive (try/except) so a failing cron never takes down FastAPI.
Alerts use the same WebSocket channel as the rest of JARVIS; optional TTS broadcast
for medium/high priority is handled in ``main`` via injected callbacks.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from llm import DeepSeekClient
from tools import run_tool
from user_profile import get_repeated_topics, load_profile

logger = logging.getLogger("jarvis.proactive")

_STATE_PATH = Path(__file__).resolve().parent / "data" / "proactive_state.json"

# Injected from main.py during lifespan
_broadcast_json: Callable[..., Awaitable[None]] | None = None
_deepseek: DeepSeekClient | None = None
_last_interaction_ts: Callable[[], float] | None = None

_scheduler: AsyncIOScheduler | None = None


def configure_proactive(
    *,
    broadcast_json: Callable[..., Awaitable[None]],
    deepseek: DeepSeekClient,
    last_interaction_ts: Callable[[], float],
) -> None:
    """Wire dependencies before ``start_scheduler()``."""
    global _broadcast_json, _deepseek, _last_interaction_ts
    _broadcast_json = broadcast_json
    _deepseek = deepseek
    _last_interaction_ts = last_interaction_ts


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.is_file():
        return {}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(data: dict[str, Any]) -> None:
    _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


async def morning_brief() -> None:
    """Daily digest: calendar + weather + headline sketch via LLM."""
    if not _broadcast_json or not _deepseek:
        return
    try:
        p = load_profile()
        if not p.city.strip():
            return
        cal = await run_tool(
            "calendar_upcoming",
            json.dumps({"days_ahead": 1, "max_events": 12}),
        )
        wx = await run_tool(
            "web_search",
            json.dumps({"query": f"current weather {p.city}"}),
        )
        topics = p.preferences.get("news_topics") or []
        news_q = (
            " ".join(str(t) for t in topics[:3])
            if topics
            else "world news headlines today"
        )
        headlines = await run_tool(
            "web_search",
            json.dumps({"query": news_q}),
        )
        sys = (
            "You are JARVIS. Produce a morning brief in at most 5 short sentences, "
            "sir-addressed, dry wit. Distill; do not quote raw URLs. Brevity."
        )
        user = f"Calendar:\n{cal[:2000]}\n\nWeather:\n{wx[:1500]}\n\nHeadlines raw:\n{headlines[:2500]}"
        data = await _deepseek.complete(
            [
                {"role": "system", "content": sys},
                {"role": "user", "content": user},
            ],
            tools=None,
            temperature=0.5,
            max_tokens=400,
        )
        text = (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        text = text.strip()
        if not text:
            return
        await _broadcast_json(
            {"type": "proactive_alert", "message": text, "priority": "high", "speak": True}
        )
    except Exception:
        logger.exception("morning_brief failed")


async def weather_watch() -> None:
    """Periodic weather check — rain likelihood and large temperature swings."""
    if not _broadcast_json:
        return
    try:
        p = load_profile()
        city = p.city.strip()
        if not city:
            return
        st = _load_state()
        today = datetime.now().date().isoformat()
        wx = await run_tool(
            "web_search",
            json.dumps({"query": f"weather forecast rain probability {city}"}),
        )
        low = wx.lower()
        rain_alert_today = st.get("rain_alert_date") == today
        pct = None
        m = re.search(r"(\d{1,3})\s*%", wx)
        if m:
            pct = int(m.group(1))
        if pct is not None and pct > 60 and not rain_alert_today:
            msg = f"Sir, rain probability near {pct}% in {city}. Umbrella advised."
            await _broadcast_json(
                {"type": "proactive_alert", "message": msg, "priority": "medium", "speak": True}
            )
            st["rain_alert_date"] = today
            _save_state(st)

        # crude swing: compare numeric temps if both present
        temps = [int(x) for x in re.findall(r"\b(\d{1,2})°?C\b", wx)]
        if len(temps) >= 2 and abs(temps[0] - temps[1]) >= 5:
            swing_key = f"swing_{today}"
            if st.get(swing_key):
                return
            msg = f"Sir, temperature shift of note in {city} — plan layers."
            await _broadcast_json(
                {"type": "proactive_alert", "message": msg, "priority": "low", "speak": False}
            )
            st[swing_key] = True
            _save_state(st)
    except Exception:
        logger.exception("weather_watch failed")


async def topic_digest() -> None:
    """Weekly digest for heavily repeated topics."""
    if not _broadcast_json or not _deepseek:
        return
    try:
        p = load_profile()
        reps = get_repeated_topics(5)
        if not reps:
            return
        for topic in reps[:3]:
            data = await _deepseek.complete(
                [
                    {
                        "role": "system",
                        "content": "You are JARVIS. Output max 3 bullet lines, sir, no preamble.",
                    },
                    {
                        "role": "user",
                        "content": f"User asked often about '{topic}' this week. Give a compact digest.",
                    },
                ],
                tools=None,
                temperature=0.4,
                max_tokens=200,
            )
            text = (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
            if not text.strip():
                continue
            msg = f"Sir, you've revisited '{topic}' several times this week. {text.strip()}"
            await _broadcast_json(
                {"type": "proactive_alert", "message": msg, "priority": "medium", "speak": False}
            )
    except Exception:
        logger.exception("topic_digest failed")


async def idle_reminder() -> None:
    """Nudge during work hours if the user has been quiet."""
    if not _broadcast_json or not _last_interaction_ts:
        return
    try:
        now = datetime.now()
        if now.weekday() >= 5:
            return
        if not (9 <= now.hour < 18):
            return
        idle = time.time() - _last_interaction_ts()
        if idle < 7200:
            return
        msg = "Sir, you've been quiet for a couple of hours. Anything I can help with?"
        await _broadcast_json(
            {"type": "proactive_alert", "message": msg, "priority": "low", "speak": False}
        )
    except Exception:
        logger.exception("idle_reminder failed")


def _parse_hhmm(s: str) -> tuple[int, int]:
    parts = s.strip().split(":")
    h = int(parts[0]) if parts else 8
    m = int(parts[1]) if len(parts) > 1 else 0
    return h % 24, m % 60


def start_scheduler() -> None:
    """Start APScheduler jobs (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return
    p = load_profile()
    h, m = _parse_hhmm(str(p.routines.get("morning_start", "08:00")))
    sched = AsyncIOScheduler()
    sched.add_job(morning_brief, "cron", hour=h, minute=m, id="morning_brief")
    sched.add_job(weather_watch, "interval", hours=2, id="weather_watch")
    sched.add_job(topic_digest, "cron", day_of_week="sun", hour=9, minute=0, id="topic_digest")
    sched.add_job(idle_reminder, "interval", hours=3, id="idle_reminder")

    sched.start()
    _scheduler = sched
    logger.info("APScheduler started (proactive agents).")


def shutdown_scheduler() -> None:
    """Stop scheduler on app shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped.")
