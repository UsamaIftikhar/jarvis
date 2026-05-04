#!/usr/bin/env python3
"""Download the default English Piper voice into ``backend/data/piper/``.

Requires: ``httpx`` (already a backend dependency). Run from repo root:

    cd backend && uv run python scripts/download_piper_voice.py

Install the ``piper`` binary separately (e.g. GitHub releases:
https://github.com/rhasspy/piper/releases ).
"""

from __future__ import annotations

import pathlib
import sys

import httpx

BASE = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "en/en_US/lessac/medium"
)
FILES = (
    "en_US-lessac-medium.onnx",
    "en_US-lessac-medium.onnx.json",
)


def main() -> int:
    root = pathlib.Path(__file__).resolve().parents[1] / "data" / "piper"
    root.mkdir(parents=True, exist_ok=True)
    with httpx.Client(timeout=600.0, follow_redirects=True) as client:
        for name in FILES:
            url = f"{BASE}/{name}?download=true"
            dest = root / name
            print(f"Downloading {name} -> {dest}", flush=True)
            with client.stream("GET", url) as r:
                r.raise_for_status()
                with open(dest, "wb") as out:
                    for chunk in r.iter_bytes(1024 * 1024):
                        if chunk:
                            out.write(chunk)
            b = dest.stat().st_size
            if b >= 1024 * 1024:
                print(f"  wrote {b / (1024 * 1024):.1f} MiB", flush=True)
            else:
                print(f"  wrote {b / 1024:.1f} KiB", flush=True)
    print("Piper voice files ready. Set PIPER_MODEL=data/piper/en_US-lessac-medium.onnx", flush=True)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except httpx.HTTPError as e:
        print(f"Download failed: {e}", file=sys.stderr)
        raise SystemExit(1) from e
