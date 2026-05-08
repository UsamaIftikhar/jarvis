from __future__ import annotations

import os
import subprocess
from typing import Any
from urllib.parse import quote_plus

import httpx

from .registry import REGISTRY, ToolEntry

_BROWSER_APP_NAMES: dict[str, str] = {
    "chrome":   "Google Chrome",
    "safari":   "Safari",
    "firefox":  "Firefox",
    "arc":      "Arc",
    "brave":    "Brave Browser",
    "edge":     "Microsoft Edge",
    "opera":    "Opera",
}

_KNOWN_BROWSERS = [
    "Google Chrome", "Safari", "Arc", "Firefox",
    "Brave Browser", "Microsoft Edge", "Opera",
]


def _frontmost_browser() -> str:
    script = (
        'tell application "System Events" to return name of '
        'first application process whose frontmost is true'
    )
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, timeout=5,
        )
        front = result.stdout.strip()
        for b in _KNOWN_BROWSERS:
            if b.lower() in front.lower():
                return b
    except Exception:  # noqa: BLE001
        pass
    return "Safari"


async def _web_search(args: dict[str, Any]) -> str:
    key = os.environ.get("TAVILY_API_KEY", "").strip()
    if not key:
        return "Web search is not configured. Set TAVILY_API_KEY in the backend `.env`."
    q = str(args.get("query", "")).strip()
    if not q:
        return "Empty search query."
    payload = {
        "api_key": key,
        "query": q,
        "search_depth": "basic",
        "max_results": 5,
        "include_answer": True,
    }
    async with httpx.AsyncClient(timeout=30.0) as client:
        r = await client.post("https://api.tavily.com/search", json=payload)
        r.raise_for_status()
        data = r.json()
    lines: list[str] = []
    if ans := data.get("answer"):
        lines.append(f"Summary: {ans}")
    for item in data.get("results") or []:
        title = item.get("title") or ""
        url = item.get("url") or ""
        content = (item.get("content") or "")[:400]
        lines.append(f"- {title}\n  {url}\n  {content}")
    return "\n".join(lines) if lines else "No results returned."


def _do_open_url(url: str, browser: str = "") -> str:
    if not url:
        return "URL cannot be empty."
    if not url.startswith(("http://", "https://", "file://")):
        url = "https://" + url
    if browser:
        app = _BROWSER_APP_NAMES.get(browser.lower(), browser)
        try:
            subprocess.run(["open", "-a", app, url], check=True, capture_output=True, timeout=10)
            return f"Opened {url} in {app}."
        except subprocess.CalledProcessError:
            pass
    subprocess.run(["open", url], check=True, capture_output=True, timeout=10)
    return f"Opened {url}."


async def _open_url(args: dict[str, Any]) -> str:
    return _do_open_url(
        str(args.get("url", "")),
        str(args.get("browser", "") or ""),
    )


async def _browser_search(args: dict[str, Any]) -> str:
    q = quote_plus(str(args.get("query", "")).strip())
    engine = str(args.get("engine", "google") or "google").lower()
    urls = {
        "google":     f"https://www.google.com/search?q={q}",
        "youtube":    f"https://www.youtube.com/results?search_query={q}",
        "bing":       f"https://www.bing.com/search?q={q}",
        "duckduckgo": f"https://duckduckgo.com/?q={q}",
    }
    return _do_open_url(urls.get(engine, urls["google"]))


async def _get_active_tab(args: dict[str, Any]) -> str:
    checks = [
        ("Google Chrome", (
            'tell application "Google Chrome"\n'
            '  set t to active tab of front window\n'
            '  return (URL of t) & " ||| " & (title of t)\n'
            'end tell'
        )),
        ("Safari", (
            'tell application "Safari"\n'
            '  set t to current tab of front window\n'
            '  return (URL of t) & " ||| " & (name of t)\n'
            'end tell'
        )),
        ("Arc", (
            'tell application "Arc"\n'
            '  set t to active tab of front window\n'
            '  return (URL of t) & " ||| " & (title of t)\n'
            'end tell'
        )),
    ]
    for browser, script in checks:
        try:
            guard = (
                f'tell application "System Events"\n'
                f'  if (count of (processes whose name is "{browser}")) > 0 then\n'
                f'    {script}\n'
                f'  end if\n'
                f'end tell'
            )
            r = subprocess.run(
                ["osascript", "-e", guard], capture_output=True, text=True, timeout=6,
            )
            out = r.stdout.strip()
            if out and " ||| " in out:
                url, title = out.split(" ||| ", 1)
                return f'Active tab: "{title}"\nURL: {url}'
        except Exception:  # noqa: BLE001
            continue
    return "No supported browser is open (tried Chrome, Safari, Arc)."


async def _browser_action(args: dict[str, Any]) -> str:
    action = str(args.get("action", ""))
    browser = _frontmost_browser()

    def _js(code: str) -> str:
        if browser == "Safari":
            return (
                f'tell application "Safari" to do JavaScript "{code}" '
                f'in current tab of front window'
            )
        return (
            f'tell application "{browser}" to execute '
            f'front window\'s active tab javascript "{code}"'
        )

    scripts: dict[str, str] = {
        "new_tab": (
            f'tell application "{browser}" to make new tab at end of tabs of front window'
            if browser != "Safari"
            else 'tell application "Safari" to tell front window to make new tab'
        ),
        "close_tab": (
            'tell application "Safari" to close current tab of front window'
            if browser == "Safari"
            else f'tell application "{browser}" to close active tab of front window'
        ),
        "back":        _js("history.back()"),
        "forward":     _js("history.forward()"),
        "reload":      _js("location.reload()"),
        "scroll_down": _js("window.scrollBy(0,600)"),
        "scroll_up":   _js("window.scrollBy(0,-600)"),
    }

    script = scripts.get(action)
    if not script:
        return f"Unknown browser action '{action}'. Valid actions: {', '.join(scripts)}."

    result = subprocess.run(
        ["osascript", "-e", script], capture_output=True, text=True, timeout=8,
    )
    if result.returncode != 0 and result.stderr.strip():
        return f"Browser action failed: {result.stderr.strip()}"

    labels = {
        "new_tab": "Opened a new tab.",
        "close_tab": "Closed the current tab.",
        "back": "Navigated back.",
        "forward": "Navigated forward.",
        "reload": "Page reloaded.",
        "scroll_down": "Scrolled down.",
        "scroll_up": "Scrolled up.",
    }
    return labels.get(action, f"Done: {action}.")


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "web_search",
            "description": (
                "Search the public web for current facts, news, or documentation. "
                "Use when the user asks for something that may have changed after your training cutoff."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "Concise English search query."},
                },
                "required": ["query"],
            },
        },
    },
    handler=_web_search,
    thinking_label="Searching…",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "open_url",
            "description": "Open a URL in the web browser. Use for 'open this site', 'go to', 'navigate to', 'open YouTube', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "Full URL including https://."},
                    "browser": {"type": "string", "description": "Optional: 'chrome', 'safari', 'firefox', 'arc'. Defaults to system default."},
                },
                "required": ["url"],
            },
        },
    },
    handler=_open_url,
    thinking_label="Opening browser…",
    terminal=True,
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "browser_search",
            "description": "Open the browser and search a query. Use when the user says 'search for X', 'look up X on YouTube', 'Google X'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The search query."},
                    "engine": {"type": "string", "description": "Search engine: 'google' (default), 'youtube', 'bing', 'duckduckgo'."},
                },
                "required": ["query"],
            },
        },
    },
    handler=_browser_search,
    thinking_label="Searching in browser…",
    terminal=True,
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "get_active_tab",
            "description": "Get the URL and title of the currently active browser tab.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    handler=_get_active_tab,
    thinking_label="Reading active tab…",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "browser_action",
            "description": "Perform a browser action: new_tab, close_tab, back, forward, reload, scroll_down, scroll_up.",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "One of: new_tab, close_tab, back, forward, reload, scroll_down, scroll_up.",
                    },
                },
                "required": ["action"],
            },
        },
    },
    handler=_browser_action,
    thinking_label="Controlling browser…",
    terminal=True,
))
