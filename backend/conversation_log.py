"""Persistent conversation history for JARVIS.

Each completed turn (user + assistant) is appended to a JSON-lines file.
On new sessions the last N turns are loaded and injected into session.history
so JARVIS remembers recent conversations across server restarts.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.history")

_DEFAULT_LOG = Path(__file__).resolve().parent / "data" / "conversation_log.jsonl"
_MAX_FILE_TURNS = 500      # keep at most 500 turns in the file (trim on startup)
_LOAD_RECENT   = int(os.environ.get("JARVIS_HISTORY_TURNS", "30"))


def _log_path() -> Path:
    p = Path(os.environ.get("JARVIS_HISTORY_PATH", str(_DEFAULT_LOG)))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def append_turn(user_text: str, assistant_text: str) -> None:
    """Append a completed turn to the persistent log."""
    record = {
        "ts":        datetime.now().isoformat(timespec="seconds"),
        "user":      user_text.strip(),
        "assistant": assistant_text.strip(),
    }
    try:
        with _log_path().open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        logger.exception("Failed to append turn to conversation log")


def load_recent_turns(n: int | None = None) -> list[dict[str, Any]]:
    """Return the last n turns as {user, assistant, ts} dicts, oldest first."""
    count = n if n is not None else _LOAD_RECENT
    path = _log_path()
    if not path.is_file():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        recent = lines[-count:] if len(lines) > count else lines
        turns = []
        for line in recent:
            line = line.strip()
            if not line:
                continue
            try:
                turns.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return turns
    except Exception:
        logger.exception("Failed to load conversation log")
        return []


def trim_log() -> None:
    """Keep only the most recent _MAX_FILE_TURNS turns to prevent unbounded growth."""
    path = _log_path()
    if not path.is_file():
        return
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
        if len(lines) <= _MAX_FILE_TURNS:
            return
        trimmed = lines[-_MAX_FILE_TURNS:]
        path.write_text("\n".join(trimmed) + "\n", encoding="utf-8")
        logger.info("Trimmed conversation log to %d turns", len(trimmed))
    except Exception:
        logger.exception("Failed to trim conversation log")


def history_summary_for_prompt(n: int | None = None) -> str:
    """Return a compact recent-history block to inject into the system prompt."""
    turns = load_recent_turns(n)
    if not turns:
        return ""
    lines = ["Recent conversation history (oldest → newest):"]
    for t in turns:
        ts = t.get("ts", "")[:16]          # YYYY-MM-DDTHH:MM
        u  = t.get("user",      "").strip()
        a  = t.get("assistant", "").strip()
        lines.append(f"[{ts}] User: {u}")
        lines.append(f"[{ts}] JARVIS: {a[:200]}{'…' if len(a) > 200 else ''}")
    return "\n".join(lines)
