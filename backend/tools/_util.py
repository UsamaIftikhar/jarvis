from __future__ import annotations

import logging
import os
import subprocess
from datetime import datetime
from pathlib import Path

try:
    from zoneinfo import ZoneInfo
except ImportError:
    ZoneInfo = None  # type: ignore[misc, assignment]

logger = logging.getLogger("jarvis.tools")


def local_now() -> datetime:
    tz_name = os.environ.get("JARVIS_TIMEZONE", "").strip()
    if tz_name and ZoneInfo is not None:
        try:
            return datetime.now(ZoneInfo(tz_name))
        except Exception:
            logger.warning("Invalid JARVIS_TIMEZONE=%r; using system local time.", tz_name)
    return datetime.now().astimezone()


def osascript_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace('"', '\\"')


def osascript(script: str, timeout: int = 5) -> str:
    result = subprocess.run(
        ["osascript", "-e", script],
        capture_output=True, text=True, timeout=timeout,
    )
    return result.stdout.strip()


def fs_root() -> Path:
    raw = os.environ.get("JARVIS_FS_ROOT", "")
    if raw.strip():
        return Path(raw).expanduser().resolve()
    return (Path.home() / "Documents" / "JARVIS").resolve()


def safe_path(rel: str) -> Path:
    root = fs_root()
    root.mkdir(parents=True, exist_ok=True)
    rel_clean = rel.replace("\\", "/").lstrip("/")
    candidate = (root / rel_clean).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("path escapes the JARVIS workspace") from exc
    return candidate
