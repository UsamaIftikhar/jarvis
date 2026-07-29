"""Public image hosting for posts that need a reachable URL (e.g. Buffer).

Buffer (and the Meta Graph API) cannot read a local file — they fetch the
image from a public URL at publish time. This module uploads a local image to
Google Drive, makes it readable by anyone-with-the-link, and returns a direct
image URL.

Drive note: share links point to a *preview* page, not the raw bytes. The
``lh3.googleusercontent.com/d/<id>`` form serves the actual image bytes and is
the most reliable direct-image URL Drive exposes. If Buffer ever rejects the
media, switch ``IMAGE_HOST`` to imgbb/catbox — only ``upload_image_public``
needs to change.

Reuses the existing Google Drive OAuth credentials from ``tools.gdrive``.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("jarvis.marketing.image_host")

_FOLDER_NAME = os.environ.get("KB_DRIVE_FOLDER", "Khas Bazaar Posts")

_MIME_BY_SUFFIX = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
    ".gif": "image/gif",
}


def _direct_url(file_id: str) -> str:
    """Direct-image URL that serves raw bytes (not a preview page)."""
    return f"https://lh3.googleusercontent.com/d/{file_id}"


def _find_or_create_folder(svc: Any, name: str) -> str | None:
    q = (
        f"name='{name}' and mimeType='application/vnd.google-apps.folder' "
        "and trashed=false"
    )
    res = svc.files().list(q=q, pageSize=1, fields="files(id)").execute()
    files = res.get("files", [])
    if files:
        return files[0]["id"]
    folder = svc.files().create(
        body={"name": name, "mimeType": "application/vnd.google-apps.folder"},
        fields="id",
    ).execute()
    return folder.get("id")


def _upload_blocking(local_path: str) -> str:
    """Upload `local_path` to Drive, make public, return a direct image URL."""
    from googleapiclient.http import MediaIoBaseUpload
    import io

    from tools.gdrive import _build_service

    path = Path(local_path)
    if not path.is_file():
        raise RuntimeError(f"Image not found: {local_path}")

    svc = _build_service()
    mimetype = _MIME_BY_SUFFIX.get(path.suffix.lower(), "image/png")

    parent_id = _find_or_create_folder(svc, _FOLDER_NAME)
    meta: dict[str, Any] = {"name": path.name}
    if parent_id:
        meta["parents"] = [parent_id]

    media = MediaIoBaseUpload(io.BytesIO(path.read_bytes()), mimetype=mimetype)
    created = svc.files().create(body=meta, media_body=media, fields="id").execute()
    file_id = created["id"]

    # Anyone-with-the-link can read — required so Buffer can fetch it.
    svc.permissions().create(
        fileId=file_id,
        body={"role": "reader", "type": "anyone"},
    ).execute()

    url = _direct_url(file_id)
    logger.info("Hosted image on Drive: %s -> %s", path.name, url)
    return url


async def upload_image_public(local_path: str) -> str:
    """Async wrapper — upload a local image and return a public direct URL."""
    return await asyncio.to_thread(_upload_blocking, local_path)
