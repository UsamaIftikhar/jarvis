from __future__ import annotations

import html as _html
import re
import subprocess
from typing import Any

from .registry import REGISTRY, ToolEntry


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return _html.unescape(text).strip()


async def _create_note(args: dict[str, Any]) -> str:
    title = str(args.get("title", "Note"))
    body = str(args.get("body", ""))
    html_title = _html.escape(title)
    html_body = _html.escape(body).replace("\n", "<br>")
    full_html = f"<h1>{html_title}</h1><div>{html_body}</div>"
    esc = full_html.replace('\\', '\\\\').replace('"', '\\"')
    script = f'''
    tell application "Notes"
        set newNote to make new note with properties {{body:"{esc}"}}
        return "Created note: " & name of newNote
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        return f"Failed to create note: {e.stderr}"


async def _search_notes(args: dict[str, Any]) -> str:
    query = str(args.get("query", ""))
    max_results = 5
    query_esc = query.replace('\\', '\\\\').replace('"', '\\"')
    script = f'''
    tell application "Notes"
        set theNotes to notes whose body contains "{query_esc}" or name contains "{query_esc}"
        set resultStr to ""
        set noteCount to 0
        repeat with aNote in theNotes
            if noteCount >= {max_results} then exit repeat
            set resultStr to resultStr & "---" & return
            set resultStr to resultStr & "Title: " & name of aNote & return
            set resultStr to resultStr & "Body: " & body of aNote & return
            set noteCount to noteCount + 1
        end repeat
        if resultStr is "" then return "No matching notes found."
        return resultStr
    end tell
    '''
    try:
        result = subprocess.run(
            ["osascript", "-e", script], capture_output=True, text=True, check=True,
        )
        raw = result.stdout.strip()
        out_blocks: list[str] = []
        for block in raw.split("---"):
            block = block.strip()
            if not block:
                continue
            lines = block.splitlines()
            out_lines: list[str] = []
            body_html: list[str] = []
            in_body = False
            for line in lines:
                if line.startswith("Body: "):
                    in_body = True
                    body_html.append(line[6:])
                elif in_body:
                    body_html.append(line)
                else:
                    out_lines.append(line)
            if body_html:
                out_lines.append("Body: " + _strip_html(" ".join(body_html)))
            out_blocks.append("\n".join(out_lines))
        return "\n---\n".join(out_blocks) if out_blocks else "No matching notes found."
    except subprocess.CalledProcessError as e:
        return f"Failed to search notes: {e.stderr}"


REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "create_note",
            "description": "Create a new note in Apple Notes. Use for 'write this down', 'take a note', etc.",
            "parameters": {
                "type": "object",
                "properties": {
                    "title": {"type": "string", "description": "A short, descriptive title for the note."},
                    "body": {"type": "string", "description": "The detailed content of the note. Basic HTML is supported."},
                },
                "required": ["title", "body"],
            },
        },
    },
    handler=_create_note,
    thinking_label="Writing note…",
    terminal=True,
    help_hint="Create a new note in Apple Notes.",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "search_notes",
            "description": "Search Apple Notes for a specific query and return the matching notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "The keyword or phrase to search for in the notes."},
                },
                "required": ["query"],
            },
        },
    },
    handler=_search_notes,
    thinking_label="Searching notes…",
    help_hint="Search Apple Notes and return matching notes.",
))
