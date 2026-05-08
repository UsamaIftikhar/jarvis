from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ToolEntry:
    definition: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    thinking_label: str
    terminal: bool = False
    help_hint: str = ""


class Registry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        name = entry.definition["function"]["name"]
        self._tools[name] = entry

    def get(self, name: str) -> ToolEntry | None:
        return self._tools.get(name)

    def all_definitions(self) -> list[dict[str, Any]]:
        return [e.definition for e in self._tools.values()]

    def thinking_labels(self) -> dict[str, str]:
        return {n: e.thinking_label for n, e in self._tools.items()}

    def terminal_tools(self) -> frozenset[str]:
        return frozenset(n for n, e in self._tools.items() if e.terminal)

    def known_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def tool_help(self) -> str:
        lines = [
            "Available tools — action must be EXACTLY one of these names, or final_answer:"
        ]
        for name, entry in self._tools.items():
            fn = entry.definition["function"]
            props = fn.get("parameters", {}).get("properties", {})
            req = set(fn.get("parameters", {}).get("required", []))
            parts = []
            for k, v in props.items():
                t = v.get("type", "any")
                opt = "" if k in req else "?"
                parts.append(f'"{k}"{opt}: {t}')
            args_str = "{" + ", ".join(parts) + "}" if parts else "{}"
            hint = f"  ← {entry.help_hint}" if entry.help_hint else ""
            lines.append(f"- {name} — args: {args_str}{hint}")
        lines.append("- final_answer — args: null (when you can answer without more tools)")
        return "\n".join(lines)


REGISTRY = Registry()
