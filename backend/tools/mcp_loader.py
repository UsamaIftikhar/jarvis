"""MCP client loader — reads mcp_servers.json, spawns each server process,
discovers its tools, and registers them into REGISTRY so the agent can use them
without any code changes.

Tool naming: <server_name>__<tool_name>  (double-underscore namespace)
This prevents collisions with built-in tools.
"""

from __future__ import annotations

import html as _html_lib
import json
import logging
import os
import re
from contextlib import AsyncExitStack
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.mcp")

_exit_stack = AsyncExitStack()
_sessions: dict[str, ClientSession] = {}


class _TextExtractor(HTMLParser):
    """Minimal HTML-to-text converter: skips style/script, adds newlines at block tags."""

    _BLOCK_TAGS = frozenset({"p", "div", "br", "tr", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th"})
    _SKIP_TAGS = frozenset({"style", "script", "head"})

    def __init__(self) -> None:
        super().__init__()
        self._parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in self._SKIP_TAGS:
            self._skip = True
        elif tag in self._BLOCK_TAGS:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP_TAGS:
            self._skip = False

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self._parts.append(data)

    def get_text(self) -> str:
        raw = "".join(self._parts)
        lines = [ln.strip() for ln in raw.splitlines()]
        out: list[str] = []
        blank = 0
        for ln in lines:
            if ln:
                blank = 0
                out.append(ln)
            else:
                blank += 1
                if blank == 1:
                    out.append("")
        return "\n".join(out).strip()


def _strip_html(text: str) -> str:
    """Convert HTML markup to readable plain text, preserving header lines."""
    parser = _TextExtractor()
    parser.feed(_html_lib.unescape(text))
    return parser.get_text()


def _result_to_str(result: Any) -> str:
    if getattr(result, "isError", False):
        parts = [b.text for b in result.content if hasattr(b, "text")]
        return "MCP tool error: " + ("\n".join(parts) or "(unknown error)")
    parts = [b.text for b in result.content if hasattr(b, "text")]
    text = "\n".join(parts) if parts else "(no output)"
    if re.search(r"<(html|table|tbody|div|span)\b", text, re.IGNORECASE):
        text = _strip_html(text)
    return text


def _make_handler(session: ClientSession, original_name: str):
    async def handler(args: dict[str, Any]) -> str:
        result = await session.call_tool(original_name, args or None)
        return _result_to_str(result)
    return handler


_TERMINAL_MCP_PREFIXES = (
    "send_", "reply_", "create_", "delete_", "archive_",
    "update_", "move_", "mark_", "trash_",
)


def _is_terminal_mcp_tool(tool_name: str) -> bool:
    """Side-effect tools that must only run once."""
    lower = tool_name.lower()
    return any(lower.startswith(p) for p in _TERMINAL_MCP_PREFIXES)


def _register_mcp_tool(server_name: str, tool: Any, session: ClientSession) -> None:
    namespaced_name = f"{server_name}__{tool.name}"
    description = tool.description or f"{tool.name} (via {server_name})"
    # MCP inputSchema IS JSON Schema — maps directly to OpenAI parameters field.
    parameters = (
        dict(tool.inputSchema)
        if tool.inputSchema
        else {"type": "object", "properties": {}, "required": []}
    )
    REGISTRY.register(ToolEntry(
        definition={
            "type": "function",
            "function": {
                "name": namespaced_name,
                "description": f"[{server_name}] {description}",
                "parameters": parameters,
            },
        },
        handler=_make_handler(session, tool.name),
        thinking_label=f"{server_name}…",
        terminal=_is_terminal_mcp_tool(tool.name),
        help_hint=f"MCP integration: {server_name}",
    ))


async def start(config_path: str | Path) -> None:
    """Spawn all MCP servers listed in config_path and register their tools."""
    path = Path(config_path)
    if not path.is_file():
        logger.info("No mcp_servers.json at %s — no external integrations loaded", path)
        return

    try:
        servers: list[dict[str, Any]] = json.loads(path.read_text())
    except Exception as exc:
        logger.error("Failed to parse %s: %s", path, exc)
        return

    if not servers:
        logger.info("mcp_servers.json is empty — no MCP servers to start")
        return

    for srv in servers:
        name = str(srv.get("name", "")).strip()
        command = str(srv.get("command", "")).strip()
        args: list[str] = srv.get("args", [])
        raw_env: dict[str, str] = srv.get("env", {})

        if not name or not command:
            logger.warning("MCP server entry missing 'name' or 'command': %s", srv)
            continue

        # Expand ${VAR} placeholders from the current process environment.
        extra_env: dict[str, str] = {}
        missing: list[str] = []
        for k, v in raw_env.items():
            expanded = os.path.expandvars(str(v))
            if expanded.startswith("${") and expanded.endswith("}"):
                missing.append(expanded[2:-1])
            else:
                extra_env[k] = expanded

        if missing:
            logger.warning(
                "MCP server '%s': env var(s) not set — skipping: %s", name, missing
            )
            continue

        # Merge extra env on top of the current process env so the child inherits PATH etc.
        merged_env = {**os.environ, **extra_env} if extra_env else None
        params = StdioServerParameters(command=command, args=args, env=merged_env)

        try:
            read, write = await _exit_stack.enter_async_context(stdio_client(params))
            session = await _exit_stack.enter_async_context(ClientSession(read, write))
            await session.initialize()
            result = await session.list_tools()

            for tool in result.tools:
                _register_mcp_tool(name, tool, session)

            _sessions[name] = session
            logger.info(
                "MCP '%s' connected — %d tool(s) registered: %s",
                name,
                len(result.tools),
                [t.name for t in result.tools],
            )
        except Exception as exc:
            logger.error("MCP server '%s' failed to start (skipping): %s", name, exc)


async def stop() -> None:
    """Gracefully close all MCP server connections."""
    if _sessions:
        logger.info("Shutting down MCP servers: %s", list(_sessions.keys()))
    await _exit_stack.aclose()
    _sessions.clear()


def connected_servers() -> list[str]:
    return list(_sessions.keys())
