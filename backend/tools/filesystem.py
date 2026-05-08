from __future__ import annotations

from typing import Any

from ._util import fs_root, safe_path
from .registry import REGISTRY, ToolEntry


async def _list_directory(args: dict[str, Any]) -> str:
    rel = str(args.get("path", ".") or ".")
    cap = max(1, min(int(args.get("max_entries", 40) or 40), 100))
    path = safe_path(rel)
    if not path.exists():
        return f"Path does not exist: {rel}"
    if not path.is_dir():
        return f"Not a directory: {rel}"
    all_entries = sorted(path.iterdir(), key=lambda p: p.name.lower())
    names = all_entries[:cap]
    rows = [f"{'DIR ' if p.is_dir() else 'FILE'} {p.name}" for p in names]
    if not rows:
        return "(empty directory)"
    more = len(all_entries) - len(names)
    tail = f"\n… and {more} more (raise max_entries)." if more > 0 else ""
    return f"Listing of {rel} under workspace {fs_root()}:\n" + "\n".join(rows) + tail


async def _read_file(args: dict[str, Any]) -> str:
    rel = str(args.get("path", ""))
    cap = max(256, min(int(args.get("max_bytes", 65536) or 65536), 512_000))
    if not rel.strip():
        return "Missing path."
    path = safe_path(rel)
    if not path.exists():
        return f"File not found: {rel}"
    if not path.is_file():
        return f"Not a file: {rel}"
    full = path.read_bytes()
    data = full[:cap]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return f"File is not valid UTF-8 (first {cap} bytes as hex): {data[:64].hex()}"
    if len(full) > cap:
        text += "\n… [truncated]"
    return text


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "list_directory",
            "description": "List files and subdirectories inside the JARVIS workspace sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "Relative path inside the workspace (use '.' for root).",
                        "default": ".",
                    },
                    "max_entries": {
                        "type": "integer",
                        "description": "Maximum entries to return (capped at 100).",
                        "default": 40,
                    },
                },
                "required": [],
            },
        },
    },
    handler=_list_directory,
    thinking_label="Listing workspace…",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a UTF-8 text file from the JARVIS workspace sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {"type": "string", "description": "Relative path to the file."},
                    "max_bytes": {
                        "type": "integer",
                        "description": "Maximum bytes to read (default 65536).",
                        "default": 65536,
                    },
                },
                "required": ["path"],
            },
        },
    },
    handler=_read_file,
    thinking_label="Reading file…",
))
