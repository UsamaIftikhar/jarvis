"""Speech-to-text via faster-whisper.

Loads the model once at startup (lazy on first transcription) and exposes
an async-friendly `transcribe()` that takes Float32 mono audio at 16 kHz
and returns the recognised text.

The actual whisper inference is CPU-blocking, so we run it in a thread
via `asyncio.to_thread` to keep the WebSocket loop responsive.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    from faster_whisper import WhisperModel

logger = logging.getLogger("jarvis.stt")

SAMPLE_RATE = 16000


def _env_model() -> str:
    return os.environ.get("WHISPER_MODEL", "base.en")


def _env_device() -> str:
    return os.environ.get("WHISPER_DEVICE", "cpu")


def _env_compute_type() -> str:
    # int8_float16 is ideal on some CUDA builds; macOS CPU falls back (see _ensure_loaded).
    return os.environ.get("WHISPER_COMPUTE_TYPE", "int8")


def _beam_size() -> int:
    raw = os.environ.get("WHISPER_BEAM_SIZE", "1").strip()
    try:
        return max(1, min(10, int(raw)))
    except ValueError:
        return 1


def _transcribe_language(model_name: str) -> str | None:
    """Whisper language hint: env overrides; `.en` checkpoints imply English."""
    env_lang = os.environ.get("WHISPER_LANGUAGE", "").strip().lower()
    if len(env_lang) == 2 and env_lang.isalpha():
        return env_lang
    if model_name.endswith(".en"):
        return "en"
    return None


@dataclass(frozen=True)
class TranscriptionResult:
    text: str
    language: str
    duration_s: float


class Transcriber:
    """Thin async wrapper around `faster_whisper.WhisperModel`."""

    def __init__(
        self,
        model_name: str | None = None,
        device: str | None = None,
        compute_type: str | None = None,
        beam_size: int | None = None,
    ) -> None:
        # Read env at construction time so callers can load_dotenv() before Transcriber().
        self.model_name = model_name if model_name is not None else _env_model()
        self.device = device if device is not None else _env_device()
        self.compute_type = compute_type if compute_type is not None else _env_compute_type()
        self.beam_size = _beam_size() if beam_size is None else max(1, min(10, beam_size))
        self._model: WhisperModel | None = None
        # The whisper model is *not* thread-safe across calls; serialise
        # transcribe() invocations with a Lock. Only one utterance is in
        # flight per WS connection anyway, but better safe than sorry.
        self._lock = threading.Lock()

    def _ensure_loaded(self) -> WhisperModel:
        if self._model is not None:
            return self._model
        # Imports deferred so the rest of the app boots even if this module
        # is imported before deps are installed (e.g. Phase 1 dev).
        from faster_whisper import WhisperModel

        requested = self.compute_type
        # int8_float16 is not supported on all CPU backends (e.g. macOS ARM).
        seen: set[str] = set()
        candidates: list[str] = []
        for ct in (requested, "float16", "int8"):
            if ct not in seen:
                seen.add(ct)
                candidates.append(ct)

        last_err: Exception | None = None
        for ct in candidates:
            try:
                logger.info(
                    "Loading Whisper model: name=%s device=%s compute=%s beam=%s",
                    self.model_name,
                    self.device,
                    ct,
                    self.beam_size,
                )
                self._model = WhisperModel(
                    self.model_name, device=self.device, compute_type=ct
                )
                if ct != requested:
                    logger.warning(
                        "WHISPER_COMPUTE_TYPE=%r unsupported on this device; using %r",
                        requested,
                        ct,
                    )
                    self.compute_type = ct
                logger.info("Whisper model loaded.")
                return self._model
            except ValueError as exc:
                last_err = exc
                self._model = None
                msg = str(exc).lower()
                if "compute type" not in msg and "backend" not in msg:
                    raise
        assert last_err is not None
        raise last_err

    async def warmup(self) -> None:
        """Force the model to load now (instead of on first transcription)."""
        await asyncio.to_thread(self._ensure_loaded)

    async def transcribe(self, pcm_f32_16k: "np.ndarray") -> TranscriptionResult:
        """Transcribe a Float32 mono numpy array at 16 kHz.

        Returns an empty string if the audio is silent or shorter than 200 ms.
        """
        import numpy as np

        if pcm_f32_16k.size == 0:
            return TranscriptionResult(text="", language="", duration_s=0.0)
        duration_s = pcm_f32_16k.size / SAMPLE_RATE
        if duration_s < 0.2:
            return TranscriptionResult(text="", language="", duration_s=duration_s)

        # Defensive: clip to [-1, 1] and ensure dtype.
        if pcm_f32_16k.dtype != np.float32:
            pcm_f32_16k = pcm_f32_16k.astype(np.float32)
        np.clip(pcm_f32_16k, -1.0, 1.0, out=pcm_f32_16k)

        return await asyncio.to_thread(self._transcribe_blocking, pcm_f32_16k, duration_s)

    def _transcribe_blocking(
        self, pcm: "np.ndarray", duration_s: float
    ) -> TranscriptionResult:
        model = self._ensure_loaded()
        with self._lock:
            segments_iter, info = model.transcribe(
                pcm,
                language=_transcribe_language(self.model_name),
                beam_size=self.beam_size,
                # Client VAD gates the recording; server VAD strips any residual
                # breath / background noise that slipped through.
                vad_filter=True,
                vad_parameters={"min_silence_duration_ms": 300, "speech_pad_ms": 400},
                # Lower than default (0.6) so whisper doesn't skip quiet words.
                no_speech_threshold=0.3,
                condition_on_previous_text=False,
            )
            text = " ".join(seg.text.strip() for seg in segments_iter).strip()
        return TranscriptionResult(
            text=text, language=info.language or "", duration_s=duration_s
        )
