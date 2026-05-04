"""Persistent user profile (Layer 2) — JSON-backed facts, preferences, and topic frequency.

Stored at ``data/user_profile.json`` relative to the backend package. Merged into
every LLM system prompt via ``get_profile_context()`` and updated after turns
via ``update_from_conversation()`` (cheap extraction call).
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from llm import DeepSeekClient

logger = logging.getLogger("jarvis.profile")

_PROFILE_PATH = Path(__file__).resolve().parent / "data" / "user_profile.json"
_WEEK_RE = re.compile(r"^\d{4}-W\d{2}$")


def _iso_week_id() -> str:
    dt = datetime.now()
    y, w, _ = dt.isocalendar()
    return f"{y}-W{w:02d}"


@dataclass
class UserProfile:
    name: str = ""
    city: str = ""
    timezone: str = "Asia/Karachi"
    preferences: dict[str, Any] = field(
        default_factory=lambda: {
            "news_topics": [],
            "temp_unit": "C",
            "response_style": "brief",
            "wake_word": False,
        }
    )
    current_projects: list[dict[str, Any]] = field(default_factory=list)
    people: dict[str, str] = field(default_factory=dict)
    routines: dict[str, str] = field(
        default_factory=lambda: {
            "morning_start": "08:00",
            "work_hours": "09:00-18:00",
            "sleep_time": "23:30",
        }
    )
    facts: list[str] = field(default_factory=list)
    topic_frequency: dict[str, int] = field(default_factory=dict)
    topic_week_id: str = field(default_factory=_iso_week_id)
    last_updated: str = ""

    def touch(self) -> None:
        self.last_updated = datetime.now().isoformat(timespec="seconds")


def _ensure_week(p: UserProfile) -> None:
    cur = _iso_week_id()
    if p.topic_week_id != cur:
        p.topic_frequency = {}
        p.topic_week_id = cur


def load_profile() -> UserProfile:
    """Load profile from disk or return defaults."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not _PROFILE_PATH.is_file():
        p = UserProfile()
        p.touch()
        save_profile(p)
        return p
    try:
        raw = json.loads(_PROFILE_PATH.read_text(encoding="utf-8"))
        p = UserProfile(
            name=str(raw.get("name", "")),
            city=str(raw.get("city", "")),
            timezone=str(raw.get("timezone", "Asia/Karachi")),
            preferences=dict(raw.get("preferences") or {}),
            current_projects=list(raw.get("current_projects") or []),
            people=dict(raw.get("people") or {}),
            routines=dict(raw.get("routines") or UserProfile().routines),
            facts=list(raw.get("facts") or []),
            topic_frequency=dict(raw.get("topic_frequency") or {}),
            topic_week_id=str(raw.get("topic_week_id") or _iso_week_id()),
            last_updated=str(raw.get("last_updated") or ""),
        )
        _ensure_week(p)
        return p
    except Exception:
        logger.exception("load_profile failed; using defaults")
        p = UserProfile()
        p.touch()
        return p


def save_profile(profile: UserProfile) -> None:
    """Persist profile to JSON."""
    _PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    profile.touch()
    _PROFILE_PATH.write_text(
        json.dumps(asdict(profile), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def get_profile_context() -> str:
    """Compact block injected into the system prompt every turn."""
    p = load_profile()
    _ensure_week(p)
    if not p.name and not p.city and not p.facts and not p.current_projects:
        return ""
    lines: list[str] = ["User profile (authoritative context):"]
    if p.name:
        lines.append(f"- Name: {p.name}")
    if p.city:
        lines.append(f"- City: {p.city}")
    if p.timezone:
        lines.append(f"- Timezone: {p.timezone}")
    if p.preferences:
        lines.append(f"- Preferences: {json.dumps(p.preferences, ensure_ascii=False)}")
    if p.current_projects:
        lines.append(f"- Current projects: {json.dumps(p.current_projects, ensure_ascii=False)}")
    if p.people:
        lines.append(f"- Known people: {json.dumps(p.people, ensure_ascii=False)}")
    if p.routines:
        lines.append(f"- Routines: {json.dumps(p.routines, ensure_ascii=False)}")
    if p.facts:
        lines.append("- Learned facts: " + "; ".join(p.facts[-12:]))
    rep = get_repeated_topics()
    if rep:
        lines.append(
            f"- Topics discussed often this week (offer a briefing if helpful): {', '.join(rep)}"
        )
    return "\n".join(lines)


def increment_topic(topic: str) -> None:
    """Increment frequency for a coarse topic label (e.g. 'weather lahore')."""
    t = topic.strip().lower()
    if len(t) < 2:
        return
    p = load_profile()
    _ensure_week(p)
    p.topic_frequency[t] = int(p.topic_frequency.get(t, 0)) + 1
    save_profile(p)


def get_repeated_topics(threshold: int = 3) -> list[str]:
    """Topics at or above ``threshold`` asks this ISO week."""
    p = load_profile()
    _ensure_week(p)
    return [k for k, v in p.topic_frequency.items() if v >= threshold]


def _merge_extraction(p: UserProfile, data: dict[str, Any]) -> None:
    if name := data.get("name"):
        p.name = str(name).strip() or p.name
    if city := data.get("city"):
        p.city = str(city).strip() or p.city
    if tz := data.get("timezone"):
        p.timezone = str(tz).strip() or p.timezone
    if isinstance(data.get("preferences"), dict):
        p.preferences.update(data["preferences"])
    if isinstance(data.get("new_people"), dict):
        p.people.update({str(k): str(v) for k, v in data["new_people"].items()})
    if isinstance(data.get("updated_projects"), list):
        for proj in data["updated_projects"]:
            if isinstance(proj, dict) and proj.get("name"):
                p.current_projects = [x for x in p.current_projects if x.get("name") != proj["name"]]
                p.current_projects.append(proj)
    if isinstance(data.get("new_facts"), list):
        for f in data["new_facts"]:
            fs = str(f).strip()
            if fs and fs not in p.facts:
                p.facts.append(fs)
    if isinstance(data.get("topics_to_increment"), list):
        _ensure_week(p)
        for t in data["topics_to_increment"]:
            tl = str(t).strip().lower()
            if len(tl) >= 2:
                p.topic_frequency[tl] = int(p.topic_frequency.get(tl, 0)) + 1


EXTRACTION_PROMPT = """You extract structured user facts from a conversation turn.
Return ONLY valid JSON (no markdown), with any subset of these keys (omit if unknown):
{
  "name": string | null,
  "city": string | null,
  "timezone": string | null,
  "preferences": object | null,
  "new_people": { "Name": "relationship" } | null,
  "updated_projects": [ { "name", "description", "deadline", "priority" } ] | null,
  "new_facts": [ string ] | null,
  "topics_to_increment": [ string ]
}
topics_to_increment: 1-3 short lowercase topic labels for what the user asked about (e.g. "weather", "project alpha").
If nothing new, return {}."""


async def update_from_conversation(
    user_text: str, assistant_reply: str, client: DeepSeekClient
) -> None:
    """Cheap LLM pass to merge new facts; safe to fire-and-forget from the main task."""
    if not user_text.strip() and not assistant_reply.strip():
        return
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": EXTRACTION_PROMPT},
        {
            "role": "user",
            "content": f"USER:\n{user_text}\n\nASSISTANT:\n{assistant_reply}",
        },
    ]
    try:
        data = await client.complete(messages, tools=None, temperature=0.2, max_tokens=400)
        msg = data.get("choices", [{}])[0].get("message") or {}
        raw = (msg.get("content") or "").strip()
        if not raw:
            return
        # Strip ```json fences if present
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            return
        p = load_profile()
        _merge_extraction(p, parsed)
        save_profile(p)
    except Exception:
        logger.exception("update_from_conversation failed")


def schedule_profile_update(
    user_text: str, assistant_reply: str, client: DeepSeekClient
) -> None:
    """Non-blocking wrapper."""
    asyncio.create_task(update_from_conversation(user_text, assistant_reply, client))


def apply_setup_fields(
    *,
    name: str,
    city: str,
    news_topics: list[str],
    timezone: str | None = None,
) -> UserProfile:
    """One-time setup from the first-launch overlay."""
    p = load_profile()
    p.name = name.strip()
    p.city = city.strip()
    if timezone:
        p.timezone = timezone.strip()
    prefs = dict(p.preferences)
    prefs["news_topics"] = news_topics
    p.preferences = prefs
    save_profile(p)
    return p


def profile_needs_setup() -> bool:
    """True until the user has at least a display name."""
    p = load_profile()
    return not p.name.strip()
