"""MCP client loader — reads mcp_servers.json, spawns each server process,
discovers its tools, and registers them into REGISTRY so the agent can use them
without any code changes.

Tool naming: <server_name>__<tool_name>  (double-underscore namespace)
This prevents collisions with built-in tools.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.mcp")

_exit_stack = AsyncExitStack()
_sessions: dict[str, ClientSession] = {}


def _result_to_str(result: Any) -> str:
    if getattr(result, "isError", False):
        parts = [b.text for b in result.content if hasattr(b, "text")]
        return "MCP tool error: " + ("\n".join(parts) or "(unknown error)")
    parts = [b.text for b in result.content if hasattr(b, "text")]
    return "\n".join(parts) if parts else "(no output)"


def _make_handler(session: ClientSession, original_name: str):
    async def handler(args: dict[str, Any]) -> str:
        result = await session.call_tool(original_name, args or None)
        return _result_to_str(result)
    return handler


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
        extra_env: dict[str, str] = srv.get("env", {})

        if not name or not command:
            logger.warning("MCP server entry missing 'name' or 'command': %s", srv)
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
