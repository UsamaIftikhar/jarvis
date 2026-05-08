from __future__ import annotations

import subprocess
from typing import Any

from ._util import osascript
from .registry import REGISTRY, ToolEntry


async def _open_app(args: dict[str, Any]) -> str:
    name = str(args.get("app_name", "")).strip()
    if not name:
        return "App name cannot be empty."
    try:
        subprocess.run(["open", "-a", name], check=True, capture_output=True, timeout=10)
        return f"Opened {name}."
    except subprocess.CalledProcessError:
        return f"Could not open '{name}'. Make sure the app is installed in /Applications."
    except FileNotFoundError:
        return "open command not available."


async def _set_volume(args: dict[str, Any]) -> str:
    mute = args.get("mute")
    if mute is True:
        osascript("set volume output muted true")
        return "Muted."
    if mute is False:
        osascript("set volume output muted false")
        return "Unmuted."
    level = max(0, min(int(args.get("percent", 50) or 50), 100))
    osascript(f"set volume output volume {level}")
    return f"Volume set to {level}%."


async def _set_brightness(args: dict[str, Any]) -> str:
    level = max(0, min(int(args.get("percent", 50) or 50), 100))
    frac = round(level / 100, 2)
    try:
        result = subprocess.run(
            ["brightness", str(frac)], capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            return f"Brightness set to {level}%."
    except FileNotFoundError:
        pass
    osascript(f'tell application "System Events" to set brightness of every display to {frac}')
    return f"Brightness set to approximately {level}%."


async def _get_clipboard(args: dict[str, Any]) -> str:
    try:
        result = subprocess.run(["pbpaste"], capture_output=True, text=True, timeout=5)
        text = result.stdout
        if not text:
            return "(Clipboard is empty)"
        if len(text) > 2000:
            return text[:2000] + "\n… [truncated]"
        return text
    except FileNotFoundError:
        return "pbpaste not available."


async def _set_clipboard(args: dict[str, Any]) -> str:
    text = str(args.get("text", ""))
    try:
        subprocess.run(["pbcopy"], input=text, text=True, check=True, timeout=5)
        preview = text[:60].replace("\n", "↵")
        return f"Copied to clipboard: {preview!r}"
    except FileNotFoundError:
        return "pbcopy not available."


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "open_app",
            "description": "Open a macOS application by name. E.g. Spotify, Chrome, VS Code, Terminal, Finder.",
            "parameters": {
                "type": "object",
                "properties": {
                    "app_name": {"type": "string", "description": "Application name as it appears in /Applications."},
                },
                "required": ["app_name"],
            },
        },
    },
    handler=_open_app,
    thinking_label="Opening app…",
    terminal=True,
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "set_volume",
            "description": "Set macOS system output volume (0–100). Also accepts 'mute' or 'unmute'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "description": "Volume level 0–100."},
                    "mute": {"type": "boolean", "description": "True to mute, false to unmute."},
                },
                "required": [],
            },
        },
    },
    handler=_set_volume,
    thinking_label="Adjusting volume…",
    terminal=True,
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "set_brightness",
            "description": "Set macOS display brightness (0–100). Requires: brew install brightness.",
            "parameters": {
                "type": "object",
                "properties": {
                    "percent": {"type": "integer", "description": "Brightness level 0–100."},
                },
                "required": ["percent"],
            },
        },
    },
    handler=_set_brightness,
    thinking_label="Adjusting brightness…",
    terminal=True,
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_clipboard",
            "description": "Read the current contents of the macOS clipboard.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_get_clipboard,
    thinking_label="Reading clipboard…",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "set_clipboard",
            "description": "Write text to the macOS clipboard.",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string", "description": "Text to copy."},
                },
                "required": ["text"],
            },
        },
    },
    handler=_set_clipboard,
    thinking_label="Copying to clipboard…",
    terminal=True,
))
