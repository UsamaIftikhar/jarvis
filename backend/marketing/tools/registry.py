"""Marketing-specific tool registry — isolated from the main JARVIS registry."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MarketingToolEntry:
    definition: dict[str, Any]
    handler: Callable[[dict[str, Any]], Awaitable[str]]
    thinking_label: str
    terminal: bool = False
    help_hint: str = ""
    passthrough: bool = False  # if True, yield tool result directly — skip final LLM reformat


class MarketingRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, MarketingToolEntry] = {}

    def register(self, entry: MarketingToolEntry) -> None:
        name = entry.definition["function"]["name"]
        self._tools[name] = entry

    def get(self, name: str) -> MarketingToolEntry | None:
        return self._tools.get(name)

    def known_names(self) -> frozenset[str]:
        return frozenset(self._tools)

    def terminal_tools(self) -> frozenset[str]:
        return frozenset(n for n, e in self._tools.items() if e.terminal)

    def thinking_labels(self) -> dict[str, str]:
        return {n: e.thinking_label for n, e in self._tools.items()}

    def tool_help(self, domain: str | None = None) -> str:
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
            desc = fn.get("description", "").strip()
            desc_str = f" — {desc}" if desc else ""
            hint = f"  ← {entry.help_hint}" if entry.help_hint else ""
            lines.append(f"- {name}{desc_str} — args: {args_str}{hint}")
        lines.append("- final_answer — args: null")
        return "\n".join(lines)


MARKETING_REGISTRY = MarketingRegistry()
