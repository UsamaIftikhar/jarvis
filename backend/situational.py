"""Situational awareness (Layer 5) — time, session, sentiment, repetition hints.

Builds an extra context block merged into the system prompt before each LLM turn.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from user_profile import UserProfile, get_repeated_topics, load_profile


def get_time_context(tz_name: str | None = None) -> str:
    """Current local context: time, weekday, flags (late night, weekend, work hours)."""
    # Default PKT for the spec; profile timezone is advisory for wording.
    try:
        from zoneinfo import ZoneInfo

        tz = ZoneInfo(tz_name or load_profile().timezone or "Asia/Karachi")
        now = datetime.now(tz)
    except Exception:
        now = datetime.now()

    hour = now.hour
    wd = now.strftime("%A")
    flags: list[str] = []
    if hour >= 23 or hour < 5:
        flags.append("late_night")
    if hour < 7:
        flags.append("early_morning")
    if now.weekday() >= 5:
        flags.append("weekend")
    if now.weekday() < 5 and 9 <= hour < 18:
        flags.append("work_hours")

    tone = ""
    if "late_night" in flags:
        tone = "It is late; be brief and gentle."
    elif "early_morning" in flags:
        tone = "Early hours; concise and calm."

    return (
        f"Time context: {now.strftime('%Y-%m-%d %H:%M')} {now.tzname() or ''}, "
        f"{wd}. Flags: {', '.join(flags) or 'none'}. {tone}".strip()
    )


def detect_sentiment(transcript: str) -> str:
    """Lightweight heuristic — no ML model."""
    t = transcript.strip().lower()
    if not t:
        return "neutral"
    if re.search(r"\b(ugh|damn|hell|stupid|broken|again|not working|why won't)\b", t):
        return "frustrated"
    if t.count("?") >= 1 and len(t.split()) <= 6:
        return "curious"
    if "!" in t:
        return "energized"
    if len(t.split()) < 4:
        return "terse"
    return "neutral"


def get_session_context(session: dict[str, Any]) -> str:
    """Narrate in-session behaviour (turn count, topics seen, brevity streak)."""
    turns = int(session.get("turn_count", 0))
    topics = session.get("topics", [])
    streak = int(session.get("short_streak", 0))
    parts = [f"Session: {turns} turn(s) so far."]
    if topics:
        parts.append(f"Topics touched: {', '.join(topics[-8:])}.")
    if streak >= 2:
        parts.append("User has sent several very short messages — prefer direct answers.")
    return " ".join(parts)


def get_repetition_alert(topic_hint: str, profile: UserProfile) -> str | None:
    """If this topic was frequent this week, nudge the model."""
    if not topic_hint.strip():
        return None
    key = topic_hint.strip().lower()
    freq = int(profile.topic_frequency.get(key, 0))
    if freq >= 3:
        return (
            f"Note: the user has revisited '{key}' about {freq} times this week — "
            "consider offering a compact briefing or checklist."
        )
    # Also surface any global repeats
    rep = get_repeated_topics(3)
    if rep and key in rep:
        return (
            f"Note: '{key}' is among the user's recurring themes this week. "
            "A digest may save time."
        )
    return None


def build_full_context(
    user_message: str,
    session: dict[str, Any],
    profile: UserProfile,
    memory_snippets: str,
) -> str:
    """Single block appended after the base JARVIS system prompt."""
    blocks: list[str] = []
    blocks.append(get_time_context(profile.timezone or None))
    blocks.append(get_session_context(session))
    sent = detect_sentiment(user_message)
    if sent != "neutral":
        blocks.append(
            f"User tone signal: {sent}. "
            + (
                "Skip banter; be direct and solution-focused."
                if sent == "frustrated"
                else "Match energy without rambling."
            )
        )
    # crude topic hint: first few words
    hint = " ".join(user_message.lower().split()[:4])
    rep = get_repetition_alert(hint, profile)
    if rep:
        blocks.append(rep)
    if memory_snippets.strip():
        blocks.append(memory_snippets.strip())
    return "\n\n".join(b for b in blocks if b.strip())
