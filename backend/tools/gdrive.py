"""Google Drive tool — full read/write access via saved OAuth credentials.

Replaces the deprecated @modelcontextprotocol/server-gdrive MCP which only
had search + read. This tool supports search, read, create folders, upload,
and delete — all using the same OAuth token from ~/.gdrive-mcp/.
"""
from __future__ import annotations

import io
import json
import logging
import os
from pathlib import Path
from typing import Any

from .registry import REGISTRY, ToolEntry

logger = logging.getLogger("jarvis.tools")

_CREDS_PATH  = Path.home() / ".gdrive-mcp" / "credentials.json"
_KEYS_PATH   = Path.home() / ".gdrive-mcp" / "gcp-oauth.keys.json"
_SCOPES      = ["https://www.googleapis.com/auth/drive"]


def _build_service():
    """Build an authenticated Drive service, refreshing the token if needed."""
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build

    if not _CREDS_PATH.is_file() or not _KEYS_PATH.is_file():
        raise RuntimeError(
            "Google Drive credentials not found. "
            "Run: GDRIVE_OAUTH_PATH=~/.gdrive-mcp/gcp-oauth.keys.json "
            "GDRIVE_CREDENTIALS_PATH=~/.gdrive-mcp/credentials.json "
            "npx -y @modelcontextprotocol/server-gdrive auth"
        )

    raw   = json.loads(_CREDS_PATH.read_text())
    keys  = json.loads(_KEYS_PATH.read_text()).get("installed", {})

    creds = Credentials(
        token         = raw.get("access_token"),
        refresh_token = raw.get("refresh_token"),
        token_uri     = keys.get("token_uri", "https://oauth2.googleapis.com/token"),
        client_id     = keys.get("client_id"),
        client_secret = keys.get("client_secret"),
        scopes        = _SCOPES,
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        raw["access_token"] = creds.token
        raw["expiry_date"]  = int(creds.expiry.timestamp() * 1000) if creds.expiry else 0
        _CREDS_PATH.write_text(json.dumps(raw, indent=2))

    return build("drive", "v3", credentials=creds, cache_discovery=False)


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def _gdrive_search(args: dict[str, Any]) -> str:
    query   = str(args.get("query", "")).strip()
    max_res = min(int(args.get("max_results", 10) or 10), 25)
    if not query:
        return "Provide a search query."
    try:
        svc    = _build_service()
        q      = f"fullText contains '{query}' and trashed = false"
        result = svc.files().list(
            q=q, pageSize=max_res,
            fields="files(id, name, mimeType, modifiedTime, webViewLink)",
        ).execute()
        files = result.get("files", [])
        if not files:
            return f"No files found for '{query}'."
        lines = [f"Found {len(files)} file(s) for '{query}':"]
        for f in files:
            mt   = f.get("modifiedTime", "")[:10]
            link = f.get("webViewLink", "")
            lines.append(f"• {f['name']} ({f['mimeType'].split('.')[-1]}) — modified {mt} — {link}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("gdrive_search failed")
        return f"Drive search error: {exc}"


# ---------------------------------------------------------------------------
# Read file
# ---------------------------------------------------------------------------

async def _gdrive_read_file(args: dict[str, Any]) -> str:
    file_id = str(args.get("file_id", "")).strip()
    if not file_id:
        return "Provide a file_id (from search results)."
    try:
        svc  = _build_service()
        meta = svc.files().get(fileId=file_id, fields="name, mimeType").execute()
        mime = meta.get("mimeType", "")
        name = meta.get("name", file_id)

        # Google Docs/Sheets/Slides → export as plain text
        export_map = {
            "application/vnd.google-apps.document":     "text/plain",
            "application/vnd.google-apps.spreadsheet":  "text/csv",
            "application/vnd.google-apps.presentation": "text/plain",
        }
        if mime in export_map:
            content = svc.files().export(
                fileId=file_id, mimeType=export_map[mime]
            ).execute()
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)
        else:
            content = svc.files().get_media(fileId=file_id).execute()
            text = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else str(content)

        return f"[{name}]\n{text[:4000]}{'…(truncated)' if len(text) > 4000 else ''}"
    except Exception as exc:
        logger.exception("gdrive_read_file failed")
        return f"Drive read error: {exc}"


# ---------------------------------------------------------------------------
# Create folder
# ---------------------------------------------------------------------------

def _find_folder_id(svc: Any, folder_name: str) -> str | None:
    """Return the ID of the first Drive folder matching folder_name, or None."""
    q = f"name='{folder_name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    res = svc.files().list(q=q, pageSize=1, fields="files(id)").execute()
    files = res.get("files", [])
    return files[0]["id"] if files else None


async def _gdrive_create_folder(args: dict[str, Any]) -> str:
    name      = str(args.get("name", "")).strip()
    parent_id = str(args.get("parent_id", "")).strip() or None
    parent_name = str(args.get("parent_folder_name", "")).strip() or None
    if not name:
        return "Provide a folder name."
    try:
        svc = _build_service()
        # Resolve parent by name if no ID given
        if not parent_id and parent_name:
            parent_id = _find_folder_id(svc, parent_name)
        meta: dict[str, Any] = {
            "name":     name,
            "mimeType": "application/vnd.google-apps.folder",
        }
        if parent_id:
            meta["parents"] = [parent_id]
        folder = svc.files().create(body=meta, fields="id, name, webViewLink").execute()
        return f"Folder '{folder['name']}' created. id={folder['id']}"
    except Exception as exc:
        logger.exception("gdrive_create_folder failed")
        return f"Drive create folder error: {exc}"


# ---------------------------------------------------------------------------
# Upload / create a text file
# ---------------------------------------------------------------------------

async def _gdrive_upload_file(args: dict[str, Any]) -> str:
    name        = str(args.get("name", "")).strip()
    content     = str(args.get("content", "")).strip()
    parent_id   = str(args.get("parent_id", "")).strip() or None
    parent_name = str(args.get("parent_folder_name", "")).strip() or None
    if not name or not content:
        return "Provide both name and content."
    try:
        from googleapiclient.http import MediaIoBaseUpload
        svc = _build_service()
        # Resolve parent folder by name if no ID given
        if not parent_id and parent_name:
            parent_id = _find_folder_id(svc, parent_name)
            if not parent_id:
                # Create the folder on the fly
                folder_meta = {
                    "name": parent_name,
                    "mimeType": "application/vnd.google-apps.folder",
                }
                folder = svc.files().create(body=folder_meta, fields="id").execute()
                parent_id = folder["id"]
        meta: dict[str, Any] = {"name": name}
        if parent_id:
            meta["parents"] = [parent_id]
        media  = MediaIoBaseUpload(
            io.BytesIO(content.encode("utf-8")), mimetype="text/plain"
        )
        result = svc.files().create(
            body=meta, media_body=media, fields="id, name"
        ).execute()
        location = f"in '{parent_name}'" if parent_name else "in Google Drive"
        return f"File '{result['name']}' created {location}. id={result['id']}"
    except Exception as exc:
        logger.exception("gdrive_upload_file failed")
        return f"Drive upload error: {exc}"


# ---------------------------------------------------------------------------
# Delete file / folder
# ---------------------------------------------------------------------------

async def _gdrive_delete(args: dict[str, Any]) -> str:
    file_id = str(args.get("file_id", "")).strip()
    if not file_id:
        return "Provide a file_id."
    try:
        svc = _build_service()
        meta = svc.files().get(fileId=file_id, fields="name").execute()
        svc.files().delete(fileId=file_id).execute()
        return f"'{meta.get('name', file_id)}' deleted from Google Drive."
    except Exception as exc:
        logger.exception("gdrive_delete failed")
        return f"Drive delete error: {exc}"


# ---------------------------------------------------------------------------
# List files in a folder
# ---------------------------------------------------------------------------

async def _gdrive_list_files(args: dict[str, Any]) -> str:
    folder_id = str(args.get("folder_id", "")).strip() or "root"
    max_res   = min(int(args.get("max_results", 15) or 15), 50)
    try:
        svc    = _build_service()
        q      = f"'{folder_id}' in parents and trashed = false"
        result = svc.files().list(
            q=q, pageSize=max_res,
            fields="files(id, name, mimeType, modifiedTime)",
            orderBy="modifiedTime desc",
        ).execute()
        files = result.get("files", [])
        if not files:
            return "No files found in this folder."
        lines = [f"{len(files)} item(s):"]
        for f in files:
            kind = "📁" if "folder" in f["mimeType"] else "📄"
            mt   = f.get("modifiedTime", "")[:10]
            lines.append(f"{kind} {f['name']} — id:{f['id']} — {mt}")
        return "\n".join(lines)
    except Exception as exc:
        logger.exception("gdrive_list_files failed")
        return f"Drive list error: {exc}"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_search",
            "description": "Search for files and documents in Google Drive by name or content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query":       {"type": "string", "description": "Search term"},
                    "max_results": {"type": "integer", "description": "Max files to return (default 10)"},
                },
                "required": ["query"],
            },
        },
    },
    handler=_gdrive_search,
    thinking_label="Searching Google Drive…",
    terminal=False,
    help_hint="full-text search across Drive files",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_read_file",
            "description": "Read the text content of a Google Drive file (Docs, Sheets, PDFs, text files). Use the file_id from gdrive_search.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "Google Drive file ID"},
                },
                "required": ["file_id"],
            },
        },
    },
    handler=_gdrive_read_file,
    thinking_label="Reading from Google Drive…",
    terminal=False,
    help_hint="reads Docs/Sheets/PDFs as text",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_list_files",
            "description": "List files in a Google Drive folder. Use folder_id='root' for My Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "folder_id":   {"type": "string", "description": "Folder ID (default: root/My Drive)"},
                    "max_results": {"type": "integer", "description": "Max items to return (default 15)"},
                },
                "required": [],
            },
        },
    },
    handler=_gdrive_list_files,
    thinking_label="Listing Google Drive files…",
    terminal=False,
    help_hint="lists files in a Drive folder",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_create_folder",
            "description": "Create a new folder in Google Drive. You can specify the parent by name instead of ID.",
            "parameters": {
                "type": "object",
                "properties": {
                    "name":               {"type": "string", "description": "Folder name to create"},
                    "parent_id":          {"type": "string", "description": "Parent folder ID (optional)"},
                    "parent_folder_name": {"type": "string", "description": "Parent folder name — will be looked up automatically (use instead of parent_id)"},
                },
                "required": ["name"],
            },
        },
    },
    handler=_gdrive_create_folder,
    thinking_label="Creating Drive folder…",
    terminal=True,
    help_hint="creates a folder in Google Drive",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_upload_file",
            "description": (
                "Create or upload a text file to Google Drive. "
                "Use parent_folder_name to place it inside a named folder — the folder will be "
                "created automatically if it does not exist. No separate search step needed."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "name":               {"type": "string", "description": "File name (e.g. 'Jarvis.docx')"},
                    "content":            {"type": "string", "description": "Text content to write into the file"},
                    "parent_id":          {"type": "string", "description": "Parent folder ID (optional, use parent_folder_name instead)"},
                    "parent_folder_name": {"type": "string", "description": "Parent folder name — folder is created automatically if missing"},
                },
                "required": ["name", "content"],
            },
        },
    },
    handler=_gdrive_upload_file,
    thinking_label="Uploading to Google Drive…",
    terminal=True,
    help_hint="creates a text file in Drive",
))

REGISTRY.register(ToolEntry(
    definition={
        "type": "function",
        "function": {
            "name": "gdrive_delete",
            "description": "Permanently delete a file or folder from Google Drive.",
            "parameters": {
                "type": "object",
                "properties": {
                    "file_id": {"type": "string", "description": "File or folder ID to delete"},
                },
                "required": ["file_id"],
            },
        },
    },
    handler=_gdrive_delete,
    thinking_label="Deleting from Google Drive…",
    terminal=True,
    help_hint="permanently deletes a Drive file/folder",
))
