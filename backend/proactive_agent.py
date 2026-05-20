"""Proactive background jobs (Layer 3) — APScheduler tasks that push HUD alerts.

All jobs are defensive (try/except) so a failing cron never takes down FastAPI.
Alerts use the same WebSocket channel as the rest of JARVIS; optional TTS
broadcast for medium/high priority is handled in ``main`` via injected callbacks.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
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
_has_clients: Callable[[], bool] | None = None

_scheduler: AsyncIOScheduler | None = None


def configure_proactive(
    *,
    broadcast_json: Callable[..., Awaitable[None]],
    deepseek: DeepSeekClient,
    last_interaction_ts: Callable[[], float],
    has_clients: Callable[[], bool],
) -> None:
    """Wire dependencies before ``start_scheduler()``."""
    global _broadcast_json, _deepseek, _last_interaction_ts, _has_clients
    _broadcast_json = broadcast_json
    _deepseek = deepseek
    _last_interaction_ts = last_interaction_ts
    _has_clients = has_clients


def _clients_connected() -> bool:
    return bool(_has_clients and _has_clients())


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


# ---------------------------------------------------------------------------
# Weather watch — every 30 min, alert on storms / rain / extreme heat
# ---------------------------------------------------------------------------

_STORM_KEYWORDS = {
    "storm", "thunderstorm", "thunder", "lightning", "cyclone", "typhoon",
    "tornado", "hurricane", "hail", "hailstorm", "blizzard", "heavy rain",
    "downpour", "flood", "flooding", "severe", "weather warning", "weather alert",
    "weather watch",
}


async def weather_watch() -> None:
    """Check weather every 30 min; push alert only on significant events."""
    if not _broadcast_json:
        return
    try:
        p = load_profile()
        city = p.city.strip()
        if not city:
            return

        wx = await run_tool(
            "web_search",
            json.dumps({"query": f"current weather {city} temperature storm rain forecast"}),
        )
        low = wx.lower()
        today = datetime.now().date().isoformat()
        st = _load_state()
        wx_st = st.setdefault("wx", {})
        alerts: list[str] = []

        # 1. Storm / severe weather
        if wx_st.get("storm_alert_date") != today:
            matched = next((kw for kw in _STORM_KEYWORDS if kw in low), None)
            if matched:
                alerts.append(
                    f"Sir, severe weather in {city} — {matched} conditions detected. "
                    "Please check conditions before going out."
                )
                wx_st["storm_alert_date"] = today

        # 2. Rain probability > 40 %
        if wx_st.get("rain_alert_date") != today:
            m = re.search(
                r"(\d{1,3})\s*%\s*(?:chance\s+of\s+)?rain|rain[^.]*?(\d{1,3})\s*%", low
            )
            if m:
                pct = int(m.group(1) or m.group(2))
                if pct > 40:
                    alerts.append(
                        f"Sir, {pct}% chance of rain in {city}. An umbrella would be wise."
                    )
                    wx_st["rain_alert_date"] = today

        # 3. Extreme heat >= 38 °C
        if wx_st.get("heat_alert_date") != today:
            temps = [int(x) for x in re.findall(r"\b(\d{2,3})\s*°?\s*[Cc]\b", wx)]
            if temps and max(temps) >= 38:
                alerts.append(
                    f"Sir, extreme heat alert — {max(temps)}°C in {city}. "
                    "Stay hydrated and limit outdoor exposure."
                )
                wx_st["heat_alert_date"] = today

        if alerts:
            _save_state(st)
        for msg in alerts:
            await _broadcast_json(
                {"type": "proactive_alert", "message": msg, "priority": "high", "speak": True}
            )
    except Exception:
        logger.exception("weather_watch failed")


# ---------------------------------------------------------------------------
# Meeting countdown — every 5 min, warn 15 min before calendar events
# ---------------------------------------------------------------------------

async def meeting_countdown() -> None:
    """Alert 15 minutes before any upcoming calendar event."""
    if not _broadcast_json:
        return
    try:
        cal = await run_tool(
            "calendar_upcoming",
            json.dumps({"days_ahead": 1, "max_events": 20}),
        )
        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        st = _load_state()
        alerted: set[str] = set(st.get("alerted_meetings", {}).get(today, []))

        for line in cal.splitlines():
            m = re.match(r"-\s*(\S+)\s*—\s*(.+)", line.strip())
            if not m:
                continue
            dt_str, title = m.group(1).strip(), m.group(2).strip()
            try:
                event_dt = datetime.fromisoformat(dt_str)
                if event_dt.tzinfo is None:
                    event_dt = event_dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

            minutes_until = (event_dt - now).total_seconds() / 60
            event_key = f"{dt_str}_{title[:40]}"

            if 0 < minutes_until <= 15 and event_key not in alerted:
                mins_int = int(minutes_until)
                suffix = "s" if mins_int != 1 else ""
                msg = f"Sir, '{title}' starts in {mins_int} minute{suffix}."
                await _broadcast_json(
                    {"type": "proactive_alert", "message": msg, "priority": "high", "speak": True}
                )
                alerted.add(event_key)
                logger.info("Meeting countdown alert fired: %s", title)

        if alerted:
            mtg = st.setdefault("alerted_meetings", {})
            mtg[today] = list(alerted)
            # Prune old days
            for old in [d for d in list(mtg.keys()) if d < today]:
                del mtg[old]
            _save_state(st)

    except Exception:
        logger.exception("meeting_countdown failed")


# ---------------------------------------------------------------------------
# Topic digest — weekly Sunday
# ---------------------------------------------------------------------------

async def topic_digest() -> None:
    """Weekly digest for heavily repeated topics."""
    if not _broadcast_json or not _deepseek:
        return
    try:
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
            text = (
                (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if not text:
                continue
            msg = f"Sir, you've revisited '{topic}' several times this week. {text}"
            await _broadcast_json(
                {"type": "proactive_alert", "message": msg, "priority": "medium", "speak": False}
            )
    except Exception:
        logger.exception("topic_digest failed")


# ---------------------------------------------------------------------------
# Morning brief — cron at user's morning_start time (default 08:00)
# ---------------------------------------------------------------------------

async def morning_brief_job() -> None:
    """Auto-deliver morning brief at 8 AM if a client is connected."""
    if not _broadcast_json or not _deepseek:
        return
    if not _clients_connected():
        return
    try:
        raw = await run_tool("morning_brief", "{}")
        if raw.startswith("[already_briefed_today]"):
            return
        data = await _deepseek.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are JARVIS. Summarize the following into a crisp spoken morning briefing "
                        "in 3–4 natural sentences. Address the user as 'sir'. "
                        "Mention today's meetings, weather headline, and any notable emails."
                    ),
                },
                {"role": "user", "content": raw},
            ],
            tools=None,
            temperature=0.4,
            max_tokens=300,
        )
        text = (
            (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if text:
            await _broadcast_json(
                {"type": "proactive_alert", "message": text, "priority": "high", "speak": True}
            )
    except Exception:
        logger.exception("morning_brief_job failed")


# ---------------------------------------------------------------------------
# Smart email scan — every 30 min, LLM judges importance
# ---------------------------------------------------------------------------

async def smart_email_scan() -> None:
    """Surface genuinely important new emails; ignore newsletters and receipts."""
    if not _broadcast_json or not _deepseek:
        return
    if not _clients_connected():
        return
    try:
        now = datetime.now()
        if not (8 <= now.hour < 22):
            return

        emails = await run_tool(
            "gmail__search_emails",
            json.dumps({"query": "is:unread", "maxResults": 10}),
        )
        if not emails or "Error" in emails[:30]:
            return

        st = _load_state()
        seen_ids: set[str] = set(st.get("seen_email_ids", []))
        found_ids = re.findall(r"ID:\s*([a-f0-9]+)", emails)
        new_ids = [eid for eid in found_ids if eid not in seen_ids]

        if not new_ids:
            return

        judgment = await _deepseek.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are JARVIS screening emails for the user. "
                        "Important = invoices, payments, deliveries, urgent messages from real people, deadlines, account alerts. "
                        "NOT important = newsletters, promotions, automated order receipts, social notifications. "
                        "If any email is important, reply with ONE sentence alerting the user (start with 'Sir,'). "
                        "If nothing important, reply with exactly: NO_ALERT"
                    ),
                },
                {"role": "user", "content": f"New unread emails:\n{emails[:2500]}"},
            ],
            tools=None,
            temperature=0.2,
            max_tokens=120,
        )
        text = (
            (judgment.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        ).strip()

        # Always update seen IDs so we don't re-evaluate the same emails
        seen_ids.update(found_ids)
        st["seen_email_ids"] = list(seen_ids)[-300:]
        _save_state(st)

        if text and not text.upper().startswith("NO_ALERT"):
            await _broadcast_json(
                {"type": "proactive_alert", "message": text, "priority": "medium", "speak": True}
            )
    except Exception:
        logger.exception("smart_email_scan failed")


# ---------------------------------------------------------------------------
# Idle reminder — enriched with actual context
# ---------------------------------------------------------------------------

async def idle_reminder() -> None:
    """Contextual nudge during work hours after 2 h of silence."""
    if not _broadcast_json or not _last_interaction_ts or not _deepseek:
        return
    if not _clients_connected():
        return
    try:
        now = datetime.now()
        if now.weekday() >= 5 or not (9 <= now.hour < 18):
            return
        if time.time() - _last_interaction_ts() < 7200:
            return

        cal = await run_tool(
            "calendar_upcoming", json.dumps({"days_ahead": 1, "max_events": 3})
        )
        emails = ""
        try:
            emails = await run_tool(
                "gmail__search_emails",
                json.dumps({"query": "is:unread", "maxResults": 3}),
            )
        except Exception:
            pass

        data = await _deepseek.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are JARVIS. The user has been idle for 2+ hours during work time. "
                        "Write ONE brief, natural check-in sentence (max 25 words). "
                        "Mention the soonest upcoming meeting or a notable unread email if relevant. "
                        "Address the user as 'sir'."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Calendar:\n{cal[:500]}\n\nUnread emails:\n{emails[:400]}",
                },
            ],
            tools=None,
            temperature=0.5,
            max_tokens=80,
        )
        text = (
            (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if text:
            await _broadcast_json(
                {"type": "proactive_alert", "message": text, "priority": "low", "speak": False}
            )
    except Exception:
        logger.exception("idle_reminder failed")


# ---------------------------------------------------------------------------
# Evening wrap-up — cron at 18:00, once per day
# ---------------------------------------------------------------------------

async def evening_wrapup() -> None:
    """End-of-day summary: remaining events + notable unread emails."""
    if not _broadcast_json or not _deepseek:
        return
    if not _clients_connected():
        return
    try:
        today = datetime.now().date().isoformat()
        st = _load_state()
        if st.get("evening_wrapup_date") == today:
            return

        cal = await run_tool(
            "calendar_upcoming", json.dumps({"days_ahead": 1, "max_events": 8})
        )
        emails = ""
        try:
            emails = await run_tool(
                "gmail__search_emails",
                json.dumps({"query": "is:unread", "maxResults": 5}),
            )
        except Exception:
            pass

        data = await _deepseek.complete(
            [
                {
                    "role": "system",
                    "content": (
                        "You are JARVIS. It's the end of the work day. "
                        "Give a 2-sentence evening wrap-up: any remaining events today or tomorrow, "
                        "and any notable unread emails worth actioning tonight. "
                        "Be brief. Address the user as 'sir'."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Calendar:\n{cal[:1000]}\n\nUnread emails:\n{emails[:800]}",
                },
            ],
            tools=None,
            temperature=0.4,
            max_tokens=200,
        )
        text = (
            (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if text:
            st["evening_wrapup_date"] = today
            _save_state(st)
            await _broadcast_json(
                {"type": "proactive_alert", "message": text, "priority": "medium", "speak": True}
            )
    except Exception:
        logger.exception("evening_wrapup failed")


# ---------------------------------------------------------------------------
# Gmail OAuth refresh — every 45 min
# ---------------------------------------------------------------------------

_GMAIL_CREDS = Path.home() / ".gmail-mcp" / "credentials.json"
_GMAIL_KEYS  = Path.home() / ".gmail-mcp" / "gcp-oauth.keys.json"


async def refresh_gmail_token() -> None:
    """Keep the Gmail OAuth access token fresh so JARVIS always has live email access."""
    if not _GMAIL_CREDS.is_file() or not _GMAIL_KEYS.is_file():
        return
    try:
        creds = json.loads(_GMAIL_CREDS.read_text())
        keys  = json.loads(_GMAIL_KEYS.read_text()).get("installed", {})
        expiry_ms = creds.get("expiry_date", 0)
        if expiry_ms / 1000 - time.time() > 600:
            return  # still fresh for > 10 min
        data = urllib.parse.urlencode(
            {
                "grant_type":    "refresh_token",
                "client_id":     keys["client_id"],
                "client_secret": keys["client_secret"],
                "refresh_token": creds["refresh_token"],
            }
        ).encode()
        req  = urllib.request.Request("https://oauth2.googleapis.com/token", data=data)
        resp = json.loads(urllib.request.urlopen(req, timeout=10).read())
        creds["access_token"] = resp["access_token"]
        creds["expiry_date"]  = int((time.time() + resp["expires_in"]) * 1000)
        _GMAIL_CREDS.write_text(json.dumps(creds, indent=2))
        logger.info("Gmail OAuth token refreshed (expires in %ss)", resp["expires_in"])
    except Exception:
        logger.exception("refresh_gmail_token failed")


# ---------------------------------------------------------------------------
# Reconnect brief — called from main.py when client reconnects after >2 h
# ---------------------------------------------------------------------------

async def reconnect_brief(away_hours: float) -> None:
    """Push a 'here's what you missed' summary when the user reconnects."""
    if not _broadcast_json or not _deepseek:
        return
    try:
        p = load_profile()
        city = (p.city or "").strip()

        cal = await run_tool(
            "calendar_upcoming",
            json.dumps({"days_ahead": 1, "max_events": 6}),
        )

        wx = ""
        if city:
            wx = await run_tool(
                "web_search",
                json.dumps({"query": f"current weather {city}"}),
            )

        emails = ""
        try:
            emails = await run_tool(
                "gmail__search_emails",
                json.dumps({"query": "is:unread", "maxResults": 3}),
            )
        except Exception:
            pass

        hours_str = f"{away_hours:.0f}"
        user_prompt = (
            f"The user has been away for about {hours_str} hour(s). "
            "Give a 2–3 sentence 'here's what you missed' catch-up. Be brief, sir-addressed.\n\n"
            f"Calendar:\n{cal[:1000]}\n\nWeather:\n{wx[:400]}\n\nEmails:\n{emails[:1000]}"
        )
        data = await _deepseek.complete(
            [
                {"role": "system", "content": "You are JARVIS. Brevity. Address user as sir."},
                {"role": "user", "content": user_prompt},
            ],
            tools=None,
            temperature=0.4,
            max_tokens=250,
        )
        text = (
            (data.get("choices", [{}])[0].get("message") or {}).get("content") or ""
        ).strip()
        if text:
            await _broadcast_json(
                {"type": "proactive_alert", "message": text, "priority": "medium", "speak": True}
            )
    except Exception:
        logger.exception("reconnect_brief failed")


# ---------------------------------------------------------------------------
# Scheduler
# ---------------------------------------------------------------------------

def start_scheduler() -> None:
    """Start APScheduler jobs (idempotent)."""
    global _scheduler
    if _scheduler is not None:
        return

    p = load_profile()
    morning_h, morning_m = 8, 0
    try:
        hh, mm = p.routines.get("morning_start", "08:00").split(":")
        morning_h, morning_m = int(hh), int(mm)
    except Exception:
        pass

    sched = AsyncIOScheduler()
    sched.add_job(weather_watch,       "interval", minutes=30,                          id="weather_watch")
    sched.add_job(meeting_countdown,   "interval", minutes=5,                           id="meeting_countdown")
    sched.add_job(topic_digest,        "cron",     day_of_week="sun", hour=9,           id="topic_digest")
    sched.add_job(idle_reminder,       "interval", hours=3,                             id="idle_reminder")
    sched.add_job(refresh_gmail_token, "interval", minutes=45,                          id="refresh_gmail_token")
    sched.add_job(morning_brief_job,   "cron",     hour=morning_h, minute=morning_m,    id="morning_brief")
    sched.add_job(smart_email_scan,    "interval", minutes=30,                          id="smart_email_scan")
    sched.add_job(evening_wrapup,      "cron",     hour=18, minute=0,                   id="evening_wrapup")
    sched.start()
    _scheduler = sched
    logger.info(
        "APScheduler started — weather(30m), meetings(5m), digest(sun), idle(3h), "
        "gmail-refresh(45m), morning-brief(%02d:%02d), email-scan(30m), evening-wrapup(18:00)",
        morning_h, morning_m,
    )


def shutdown_scheduler() -> None:
    """Stop scheduler on app shutdown."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("APScheduler stopped.")
