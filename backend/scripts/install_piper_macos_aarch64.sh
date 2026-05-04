#!/usr/bin/env bash
# Download official Piper bundle for Apple Silicon (arm64). Includes piper,
# espeak-ng data, and ONNX runtime — no Homebrew espeak-ng required.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VER="2023.11.14-2"
URL="https://github.com/rhasspy/piper/releases/download/${VER}/piper_macos_aarch64.tar.gz"
DEST="${ROOT}/data/piper/macos-aarch64"
mkdir -p "${DEST}"
echo "Downloading Piper ${VER} (macOS arm64) -> ${DEST} ..."
curl -fsSL "${URL}" | tar -xzf - -C "${DEST}"
BIN="${DEST}/piper/piper"
if [[ ! -f "${BIN}" ]]; then
  echo "error: expected ${BIN} after extract" >&2
  exit 1
fi
chmod +x "${BIN}" 2>/dev/null || true
echo ""
echo "Architecture check:"
file "${BIN}" || true
echo ""
echo "If the line above says **x86_64** on an Apple Silicon Mac, the GitHub archive is Intel-only."
echo "Then uncomment in backend/.env:"
echo "  PIPER_PREFIX_ARGS=arch -x86_64"
echo "  PIPER_DYLD_LIBRARY_PATH=/usr/local/opt/espeak-ng/lib"
echo "and install espeak-ng for x86 via Rosetta Homebrew (see .env.example)."
echo ""
echo "In backend/.env set (paths relative to backend/):"
echo "  PIPER_CMD=data/piper/macos-aarch64/piper/piper"
echo "Keep PIPER_MODEL=data/piper/en_US-lessac-medium.onnx (or run download_piper_voice.py)."
