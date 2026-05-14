"""Text-to-speech backends for JARVIS.

- **ElevenLabs** (cloud): streaming PCM at 16 kHz via HTTP.
- **Piper** (local): runs the ``piper`` CLI, reads WAV output, resamples to
  16 kHz int16 mono for the same WebSocket binary protocol.

Choose with ``JARVIS_TTS_PROVIDER=piper`` or ``elevenlabs``. Sentence chunking
(``chunk_text_for_tts``) is shared so playback can start before the full reply
is finished.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
import signal
import time
import re
import shlex
import shutil
import subprocess
import tempfile
import threading
import wave
from collections.abc import AsyncIterator

import httpx
import numpy as np

logger = logging.getLogger("jarvis.tts")


def truthy_env(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")

_BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))

# 16 kHz int16 mono. Each second of audio = 32_000 bytes (wire format to browser).
TTS_SAMPLE_RATE = 16000
TTS_BYTES_PER_SAMPLE = 2

# Split only at true sentence ends; let Piper handle comma/semicolon prosody itself.
_SENTENCE_BOUNDARY = re.compile(r"(?<=[\.\!\?])\s+")


def chunk_text_for_tts(text: str, *, min_chars: int = 60) -> list[str]:
    """Split a long string into TTS-friendly chunks at clause boundaries.

    Each chunk will be at least `min_chars` long unless it's the final
    chunk. Keeps natural prosody by ending at punctuation.
    """
    text = text.strip()
    if not text:
        return []
    parts = _SENTENCE_BOUNDARY.split(text)
    chunks: list[str] = []
    buf = ""
    for part in parts:
        if buf:
            buf += " "
        buf += part
        if len(buf) >= min_chars:
            chunks.append(buf.strip())
            buf = ""
    if buf.strip():
        chunks.append(buf.strip())
    return chunks


def _resolve_path_maybe_relative(path: str) -> str:
    path = path.strip()
    if not path:
        return ""
    if os.path.isabs(path):
        return path
    return os.path.normpath(os.path.join(_BACKEND_DIR, path))


def _wav_bytes_to_int16_mono(wav_bytes: bytes) -> tuple[bytes, int]:
    """Return int16-le mono PCM bytes and sample rate."""
    bio = io.BytesIO(wav_bytes)
    with wave.open(bio, "rb") as wf:
        sr = wf.getframerate()
        nch = wf.getnchannels()
        sw = wf.getsampwidth()
        raw = wf.readframes(wf.getnframes())
    if sw != 2:
        raise RuntimeError(f"Piper WAV sample width {sw} not supported (need 16-bit PCM)")
    arr = np.frombuffer(raw, dtype="<i2").copy()
    if nch == 2:
        arr = arr.reshape(-1, 2).mean(axis=1).astype(np.int16)
    elif nch != 1:
        raise RuntimeError(f"Piper WAV has {nch} channels; need 1 or 2")
    return arr.tobytes(), sr


def _resample_int16_pcm(pcm: bytes, src_sr: int, dst_sr: int) -> bytes:
    if src_sr == dst_sr or not pcm:
        return pcm
    arr = np.frombuffer(pcm, dtype=np.int16).astype(np.float64)
    n_src = arr.size
    if n_src == 0:
        return pcm
    n_dst = max(1, int(round(n_src * dst_sr / src_sr)))
    x_src = np.arange(n_src, dtype=np.float64)
    x_new = np.linspace(0.0, n_src - 1.0, num=n_dst)
    out = np.interp(x_new, x_src, arr).astype(np.int16)
    return out.tobytes()


def _read_piper_model_sample_rate(model_path: str) -> int:
    """Read native sample rate from Piper model's .onnx.json sidecar."""
    import json
    for cfg_path in (model_path + ".json", model_path.replace(".onnx", ".onnx.json")):
        if os.path.isfile(cfg_path):
            try:
                with open(cfg_path) as f:
                    return int(json.load(f)["audio"]["sample_rate"])
            except Exception:
                pass
    return 22050  # Piper default when no config found


async def _piper_stream_raw(
    piper_cmd: str,
    model_path: str,
    config_path: str | None,
    text: str,
    timeout_s: float,
    length_scale: float,
) -> AsyncIterator[bytes]:
    """Spawn Piper with --output-raw and yield int16 PCM chunks as they arrive.

    Audio starts flowing before synthesis is complete — first chunk typically
    arrives within ~100 ms on Apple Silicon, cutting perceived latency.
    """
    exe, cwd = _piper_exe_and_cwd(piper_cmd)
    if not os.path.isfile(exe):
        raise RuntimeError(
            f"Piper executable not found: {exe!r}. Set PIPER_CMD in .env or "
            "run scripts/install_piper_macos_aarch64.sh"
        )

    native_sr = _read_piper_model_sample_rate(model_path)
    prefix = shlex.split(os.environ.get("PIPER_PREFIX_ARGS", "").strip())
    cmd = [*prefix, exe, "--model", model_path, "--output-raw",
           "--length_scale", str(length_scale)]
    if config_path:
        cmd.extend(["--config", config_path])

    popen_kw: dict = {
        "stdin": subprocess.PIPE,
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
        "cwd": cwd,
        "env": _piper_subprocess_env(),
    }
    if os.name == "posix":
        popen_kw["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kw)  # noqa: S603

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(maxsize=32)

    def _reader() -> None:
        try:
            assert proc.stdin is not None
            proc.stdin.write((text + "\n").encode("utf-8"))
            proc.stdin.close()
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(4096)
                if not chunk:
                    break
                loop.call_soon_threadsafe(queue.put_nowait, chunk)
        except Exception as exc:
            logger.warning("Piper reader thread error: %s", exc)
        finally:
            loop.call_soon_threadsafe(queue.put_nowait, None)

    reader = threading.Thread(target=_reader, daemon=True, name="jarvis-piper-raw")
    reader.start()

    need_resample = native_sr != TTS_SAMPLE_RATE
    t0 = time.monotonic()
    try:
        while True:
            remaining = timeout_s - (time.monotonic() - t0)
            if remaining <= 0:
                _kill_piper_proc_tree(proc)
                raise RuntimeError(f"Piper timed out after {timeout_s}s")
            chunk = await asyncio.wait_for(queue.get(), timeout=remaining)
            if chunk is None:
                break
            if need_resample:
                chunk = await asyncio.to_thread(_resample_int16_pcm, chunk, native_sr, TTS_SAMPLE_RATE)
            yield chunk
    finally:
        await asyncio.to_thread(reader.join, 2)
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            _kill_piper_proc_tree(proc)

    if proc.returncode not in (0, None):
        assert proc.stderr is not None
        err = proc.stderr.read().decode("utf-8", errors="ignore")[:500]
        raise RuntimeError(f"Piper exited {proc.returncode}: {err}")


def _piper_subprocess_env() -> dict[str, str]:
    """Optional ``PIPER_DYLD_LIBRARY_PATH`` (``os.pathsep``-separated dirs) merged into ``DYLD_LIBRARY_PATH``.

    Do **not** auto-inject Homebrew ``espeak-ng`` — an Intel ``piper`` binary + arm64
    Homebrew libs produces ``incompatible architecture`` on Apple Silicon.
    """
    env = dict(os.environ)
    manual = os.environ.get("PIPER_DYLD_LIBRARY_PATH", "").strip()
    if not manual:
        return env
    lib_dirs = [p.strip() for p in manual.split(os.pathsep) if p.strip() and os.path.isdir(p.strip())]
    if not lib_dirs:
        return env
    extra = os.pathsep.join(lib_dirs)
    prev = env.get("DYLD_LIBRARY_PATH", "")
    env["DYLD_LIBRARY_PATH"] = f"{extra}{os.pathsep}{prev}" if prev else extra
    return env


def _default_bundled_piper_exe() -> str:
    """Official macOS tarball layout under ``backend/data/piper/macos-aarch64/``."""
    return os.path.normpath(
        os.path.join(_BACKEND_DIR, "data", "piper", "macos-aarch64", "piper", "piper")
    )


def log_piper_apple_silicon_hint_if_needed() -> None:
    """Log a single prominent hint when Piper looks like Intel-only on ARM Macs."""

    import platform

    if platform.system() != "Darwin" or platform.machine() != "arm64":
        return
    prov = os.environ.get("JARVIS_TTS_PROVIDER", "piper").strip().lower()
    if prov in ("11labs", "eleven", "elevenlabs"):
        return

    cmd = os.environ.get("PIPER_CMD", "piper").strip()
    exe, _cwd = _piper_exe_and_cwd(cmd, quiet=True)
    if not os.path.isfile(exe):
        return

    try:
        r = subprocess.run(
            ["file", "-b", exe],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        banner = (r.stdout or "") + (r.stderr or "")
    except OSError:
        return

    if "x86_64" not in banner.lower():
        return

    prefix = os.environ.get("PIPER_PREFIX_ARGS", "").strip()
    dyld = os.environ.get("PIPER_DYLD_LIBRARY_PATH", "").strip()

    if prefix and dyld:
        logger.info(
            "Piper executable is Intel (x86_64); using Rosetta/env (PIPER_PREFIX_ARGS + "
            "PIPER_DYLD_LIBRARY_PATH are set). If audio is still silent, verify "
            "`/usr/local/opt/espeak-ng/lib` exists (x86 Homebrew), not `/opt/homebrew`."
        )
        return

    logger.warning(
        "Piper at %s is an Intel binary on Apple Silicon. Without espeak-ng the process "
        "will fail dyld lookup for libespeak-ng. Fix: install x86 Homebrew espeak-ng under "
        "/usr/local, then set `PIPER_PREFIX_ARGS=arch -x86_64` and "
        "`PIPER_DYLD_LIBRARY_PATH=/usr/local/opt/espeak-ng/lib` — see `.env.example`. "
        "(have PREFIX_ARGS=%s DYLD=%s)",
        exe,
        repr(prefix or "(unset)"),
        repr(dyld or "(unset)"),
    )


def _piper_exe_and_cwd(piper_cmd: str, *, quiet: bool = False) -> tuple[str, str | None]:
    """Resolve executable path and working directory (bundle dir for data + dylibs)."""
    token = piper_cmd.strip()
    if not token:
        token = "piper"
    if os.path.sep in token or (os.altsep and os.altsep in token):
        exe = os.path.abspath(token)
    else:
        found = shutil.which(token)
        exe = os.path.abspath(found) if found else token
    if os.path.isfile(exe):
        return exe, os.path.dirname(exe)
    bundled = _default_bundled_piper_exe()
    if token == "piper" and os.path.isfile(bundled):
        if not quiet:
            logger.warning(
                "PIPER_CMD=%r not on PATH — using bundled Piper at %s", piper_cmd, bundled
            )
        return bundled, os.path.dirname(bundled)
    return piper_cmd, None


def _kill_piper_proc_tree(proc: subprocess.Popen) -> None:
    """Hard-kill Piper and any ``arch``/child wrapper (``subprocess.run`` timeout alone can hang)."""

    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        if os.name == "posix" and pid is not None and pid > 0:
            pgid = os.getpgid(pid)
            os.killpg(pgid, signal.SIGKILL)
            return
    except (PermissionError, ProcessLookupError):
        pass
    try:
        proc.kill()
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        pass


def _piper_run_blocking(
    piper_cmd: str,
    model_path: str,
    config_path: str | None,
    text: str,
    timeout_s: float,
    length_scale: float = 0.9,
) -> bytes:
    fd, out_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)
    try:
        exe, cwd = _piper_exe_and_cwd(piper_cmd)
        if not os.path.isfile(exe):
            raise RuntimeError(
                f"Piper executable not found: {exe!r}. Set PIPER_CMD in .env (only once — "
                "duplicate keys override earlier ones) or run scripts/install_piper_macos_aarch64.sh"
            )
        prefix = shlex.split(os.environ.get("PIPER_PREFIX_ARGS", "").strip())
        cmd = [*prefix, exe, "--model", model_path, "--output_file", out_path,
               "--length_scale", str(length_scale)]
        if config_path:
            cmd.extend(["--config", config_path])
        t0 = time.monotonic()
        done_evt = threading.Event()

        hb_s = os.environ.get("PIPER_LOG_HEARTBEAT_S", "15").strip() or "15"
        hb_interval = max(5.0, float(hb_s))

        def _heartbeat() -> None:
            while True:
                if done_evt.wait(timeout=hb_interval):
                    return
                wall = time.monotonic() - t0
                if wall > timeout_s + 5:
                    logger.warning(
                        "Piper heartbeat past timeout (elapsed ~%.0fs, limit=%ss) — "
                        "configured limit should already have aborted; subprocess kill may lag.",
                        wall,
                        timeout_s,
                    )
                else:
                    logger.info(
                        "Piper subprocess still running (~%.0fs elapsed, timeout=%ss) — "
                        "Intel/Rosetta + first ONNX compile can sit here for minutes; logs are not frozen.",
                        wall,
                        timeout_s,
                    )

        hb = threading.Thread(
            target=_heartbeat,
            name="jarvis-piper-hb",
            daemon=True,
        )
        hb.start()
        popen_kw = {
            "stdin": subprocess.PIPE,
            "stdout": subprocess.PIPE,
            "stderr": subprocess.PIPE,
            "cwd": cwd,
            "env": _piper_subprocess_env(),
        }
        # New session ⇒ own process group so ``killpg`` can drop ``arch`` + ``piper`` together.
        if os.name == "posix":
            popen_kw["start_new_session"] = True

        proc = subprocess.Popen(cmd, **popen_kw)  # noqa: S603 — argv from env-approved Piper setup
        in_bytes = (text + "\n").encode("utf-8")
        stderr: bytes | None = b""
        try:
            try:
                _unused_out, stderr = proc.communicate(input=in_bytes, timeout=timeout_s)
            except subprocess.TimeoutExpired:
                elapsed = time.monotonic() - t0
                # Stop heartbeat immediately; reap can take another ~10s.
                done_evt.set()
                logger.error(
                    "Piper timed out after %.1fs (limit=%ss) — killing process group (arch+possible children).",
                    elapsed,
                    timeout_s,
                )
                _kill_piper_proc_tree(proc)
                try:
                    _unused_out, stderr = proc.communicate(timeout=10)
                except Exception:
                    stderr = stderr or b""
                raise RuntimeError(
                    f"Piper did not finish within {timeout_s}s on this machine. "
                    "If it already ran many minutes, raising PIPER_TIMEOUT_S usually will not help—the "
                    "x86/Rosetta ONNX path is stuck or unusably slow here. Use a *-low ONNX voice, "
                    "JARVIS_TTS_PROVIDER=elevenlabs, or a native ARM Piper build to drop Rosetta."
                ) from None
        finally:
            done_evt.set()

        elapsed = time.monotonic() - t0
        if elapsed >= 1.5:
            logger.info(
                "Piper subprocess returned in %.1fs (rc=%s)",
                elapsed,
                proc.returncode,
            )
        else:
            logger.debug(
                "Piper subprocess returned in %.1fs (rc=%s)",
                elapsed,
                proc.returncode,
            )
        if proc.returncode != 0:
            err_raw = stderr if stderr else b""
            err = err_raw.decode("utf-8", errors="ignore")[:1200]
            low = err.lower()
            hint = ""
            if "library not loaded" in low or "libespeak" in low:
                hint = (
                    " On Apple Silicon, the bundled Piper build is usually x86_64: "
                    "install Intel Homebrew espeak-ng under `/usr/local`, run Piper under Rosetta "
                    "(`PIPER_PREFIX_ARGS=arch -x86_64`), set "
                    "`PIPER_DYLD_LIBRARY_PATH=/usr/local/opt/espeak-ng/lib`. See `.env.example`."
                )
            raise RuntimeError(f"Piper exited {proc.returncode}: {err}{hint}")
        with open(out_path, "rb") as f:
            data = f.read()
        if len(data) < 100:
            raise RuntimeError("Piper produced an empty or tiny WAV file")
        return data
    finally:
        try:
            os.unlink(out_path)
        except OSError:
            pass


class ElevenLabsClient:
    def __init__(
        self,
        api_key: str | None = None,
        voice_id: str | None = None,
        model: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("ELEVENLABS_API_KEY", "")
        self.voice_id = voice_id or os.environ.get("ELEVENLABS_VOICE_ID", "")
        self.model = model or os.environ.get("ELEVENLABS_MODEL", "eleven_flash_v2_5")
        self.timeout_s = timeout_s
        if not self.api_key:
            logger.warning("ELEVENLABS_API_KEY missing — TTS will fail")
        if not self.voice_id:
            logger.warning("ELEVENLABS_VOICE_ID missing — TTS will fail")

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        """Yield Int16 LE 16 kHz mono PCM chunks for a single piece of text."""
        text = text.strip()
        if not text:
            return

        url = (
            f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice_id}/stream"
            f"?output_format=pcm_{TTS_SAMPLE_RATE}"
        )
        headers = {
            "xi-api-key": self.api_key,
            "Content-Type": "application/json",
            "Accept": "audio/pcm",
        }
        payload = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {
                "stability": 0.4,
                "similarity_boost": 0.75,
                "style": 0.15,
                "use_speaker_boost": True,
            },
        }

        async with httpx.AsyncClient(timeout=self.timeout_s) as client:
            async with client.stream(
                "POST", url, headers=headers, json=payload
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", "ignore")
                    raise httpx.HTTPStatusError(
                        f"ElevenLabs error {response.status_code}: {body[:300]}",
                        request=response.request,
                        response=response,
                    )
                async for chunk in response.aiter_bytes(chunk_size=4096):
                    if chunk:
                        yield chunk


class PiperClient:
    """Local TTS via the ``piper`` executable (https://github.com/rhasspy/piper)."""

    def __init__(
        self,
        *,
        piper_cmd: str | None = None,
        model_path: str | None = None,
        config_path: str | None = None,
        timeout_s: float | None = None,
    ) -> None:
        self.piper_cmd = (piper_cmd or os.environ.get("PIPER_CMD", "piper")).strip()
        raw_model = (model_path or os.environ.get("PIPER_MODEL", "")).strip()
        self.model_path = _resolve_path_maybe_relative(raw_model)
        raw_cfg = (config_path or os.environ.get("PIPER_CONFIG", "")).strip()
        # Only pass ``--config`` when set explicitly; Piper auto-loads ``*.onnx.json``
        # beside the model when present.
        self.cli_config_path: str | None = (
            _resolve_path_maybe_relative(raw_cfg) if raw_cfg else None
        )
        self.timeout_s = float(timeout_s or os.environ.get("PIPER_TIMEOUT_S", "120"))
        # < 1.0 = faster speech, > 1.0 = slower. Default 0.9 is slightly snappier.
        self.length_scale = float(os.environ.get("PIPER_LENGTH_SCALE", "0.9"))

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        """Synthesize one chunk; yield 16 kHz int16 mono PCM in 4k blocks."""
        text = text.strip()
        if not text:
            return
        if not self.model_path or not os.path.isfile(self.model_path):
            raise RuntimeError(
                "PIPER_MODEL must point to an existing Piper .onnx file "
                "(see .env.example). Install Piper and download a voice."
            )

        logger.info("Piper: synthesizing (~%d chars)", len(text))
        got_any = False
        async for chunk in _piper_stream_raw(
            self.piper_cmd,
            self.model_path,
            self.cli_config_path,
            text,
            self.timeout_s,
            self.length_scale,
        ):
            got_any = True
            yield chunk
        if not got_any:
            raise RuntimeError(
                "Piper produced zero samples — check Piper binary "
                "(Rosetta + PIPER_PREFIX_ARGS / PIPER_DYLD_LIBRARY_PATH on Apple Silicon), "
                "voice model path, or run with logging enabled."
            )


class PiperPythonClient:
    """Piper TTS via the ``piper-tts`` Python package — no CLI binary or dylibs needed."""

    def __init__(self, model_path: str | None = None) -> None:
        raw = (model_path or os.environ.get("PIPER_MODEL", "")).strip()
        self.model_path = _resolve_path_maybe_relative(raw)
        self._voice: object = None
        self._load_lock = threading.Lock()

    def _get_voice(self) -> object:
        with self._load_lock:
            if self._voice is None:
                try:
                    from piper import PiperVoice  # type: ignore[import]
                except ImportError as exc:
                    raise RuntimeError(
                        "piper-tts not installed — run `uv add piper-tts`"
                    ) from exc
                if not self.model_path or not os.path.isfile(self.model_path):
                    raise RuntimeError(
                        f"PIPER_MODEL not found: {self.model_path!r}. "
                        "Download a voice with scripts/download_piper_voice.py."
                    )
                logger.info("Piper: loading voice model %s", self.model_path)
                self._voice = PiperVoice.load(self.model_path)
                logger.info("Piper: voice model loaded")
        return self._voice

    def _synthesize_blocking(self, text: str) -> list[bytes]:
        voice = self._get_voice()
        return [chunk.audio_int16_bytes for chunk in voice.synthesize(text)]  # type: ignore[attr-defined]

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        text = text.strip()
        if not text:
            return
        logger.info("Piper: synthesizing (~%d chars)", len(text))
        pcm_chunks = await asyncio.to_thread(self._synthesize_blocking, text)
        for chunk in pcm_chunks:
            if chunk:
                yield chunk


async def warmup_piper_if_needed(client: ElevenLabsClient | PiperClient | PiperPythonClient | SayClient) -> None:
    """Pre-load the Piper voice model at startup so first-turn latency is low."""

    if not isinstance(client, (PiperClient, PiperPythonClient)):
        return
    logger.info(
        "Piper warmup: loading voice model — starting…",
    )
    t0 = time.monotonic()
    try:
        async for pcm in client.stream_pcm("Hi."):
            if pcm:
                break
        logger.info("Piper warmup complete in %.1fs", time.monotonic() - t0)
    except Exception:
        logger.exception(
            "Piper warmup failed — voice TTS will error until Piper and espeak-ng are fixed",
        )


class SayClient:
    """macOS built-in TTS via ``say`` + ``afconvert``. Free, zero-dependency."""

    def __init__(self, voice: str | None = None) -> None:
        self.voice = (voice or os.environ.get("SAY_VOICE", "Daniel")).strip()

    def _run_blocking(self, text: str) -> bytes:
        fd_a, aiff_path = tempfile.mkstemp(suffix=".aiff")
        fd_w, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd_a)
        os.close(fd_w)
        try:
            cmd = ["say", text, "-o", aiff_path]
            if self.voice:
                cmd.extend(["-v", self.voice])
            r = subprocess.run(cmd, capture_output=True, timeout=30, check=False)
            if r.returncode != 0:
                raise RuntimeError(f"say exited {r.returncode}: {r.stderr.decode('utf-8','ignore')[:300]}")
            r2 = subprocess.run(
                ["afconvert", "-f", "WAVE", "-d", "LEI16@16000", aiff_path, wav_path],
                capture_output=True, timeout=15, check=False,
            )
            if r2.returncode != 0:
                raise RuntimeError(f"afconvert exited {r2.returncode}: {r2.stderr.decode('utf-8','ignore')[:300]}")
            with open(wav_path, "rb") as f:
                return f.read()
        finally:
            for p in (aiff_path, wav_path):
                try:
                    os.unlink(p)
                except OSError:
                    pass

    async def stream_pcm(self, text: str) -> AsyncIterator[bytes]:
        text = text.strip()
        if not text:
            return
        logger.info("say: synthesizing (~%d chars) voice=%s", len(text), self.voice)
        wav_bytes = await asyncio.to_thread(self._run_blocking, text)
        pcm, sr = await asyncio.to_thread(_wav_bytes_to_int16_mono, wav_bytes)
        if sr != TTS_SAMPLE_RATE:
            pcm = await asyncio.to_thread(_resample_int16_pcm, pcm, sr, TTS_SAMPLE_RATE)
        step = 4096
        for i in range(0, len(pcm), step):
            yield pcm[i : i + step]


def build_tts_client() -> ElevenLabsClient | PiperPythonClient | PiperClient | SayClient:
    """Select TTS from ``JARVIS_TTS_PROVIDER`` (``piper``, ``say``, or ``elevenlabs``)."""
    provider = os.environ.get("JARVIS_TTS_PROVIDER", "piper").strip().lower()
    if provider in ("11labs", "eleven", "elevenlabs"):
        logger.info("TTS provider: elevenlabs")
        return ElevenLabsClient()
    if provider == "piper":
        logger.info("TTS provider: piper-python (model=%s)", os.environ.get("PIPER_MODEL", ""))
        return PiperPythonClient()
    if provider == "piper-cli":
        logger.info("TTS provider: piper-cli (cmd=%s model=%s)", os.environ.get("PIPER_CMD", "piper"), os.environ.get("PIPER_MODEL", ""))
        return PiperClient()
    logger.info("TTS provider: say (voice=%s)", os.environ.get("SAY_VOICE", "Daniel"))
    return SayClient()
