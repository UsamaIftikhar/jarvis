"""FastAPI entrypoint for the JARVIS backend.

WebSocket protocol (text frames are JSON):

    Client -> Server
        TEXT  {"type":"ping","ts":int}
        TEXT  {"type":"echo","text":str}                       (debug helper)
        TEXT  {"type":"utterance_start","sample_rate":int}     (begin streaming)
        TEXT  {"type":"utterance_end"}                         (commit + process)
        TEXT  {"type":"cancel"}                                (drop in-flight work)
        BINARY  Int16 LE PCM mono at the declared sample rate

    Server -> Client
        TEXT  {"type":"hello","server":str,"version":str,"needs_profile_setup":bool}
        TEXT  {"type":"thinking_step","label":str}
        TEXT  {"type":"proactive_alert","message":str,"priority":str,"speak":bool}
        TEXT  {"type":"reminder","message":str,"name":str|omit,"speak":bool}
        TEXT  {"type":"pong","ts":int}
        TEXT  {"type":"echo","text":str}
        TEXT  {"type":"thinking"}
        TEXT  {"type":"transcript","text":str}                 (final user STT)
        TEXT  {"type":"token","text":str}                      (LLM token delta)
        TEXT  {"type":"final","text":str}                      (full LLM reply)
        TEXT  {"type":"speaking_start","sample_rate":int}      (about to send PCM)
        BINARY  Int16 LE PCM mono at TTS_SAMPLE_RATE
        TEXT  {"type":"speaking_end"}
        TEXT  {"type":"error","message":str}
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
import time
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, cast

from dotenv import load_dotenv

# Before any app submodule import: they may read os.environ at import time.
load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), ".env"))
# HuggingFace tokenizers uses a Rust thread pool; after fork() it warns / can deadlock.
# Default off; override in .env with TOKENIZERS_PARALLELISM=true if you need tokenizer threads.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")

import numpy as np
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from agent_loop import run_react_agent
from llm import JARVIS_SYSTEM_PROMPT, ChatMessage, DeepSeekClient
from memory import describe_memory_status, get_memory
from proactive_agent import configure_proactive, shutdown_scheduler, start_scheduler
from tools import configure_tools, mcp_loader
from watcher import shutdown_log_watcher, start_log_watcher
from situational import build_full_context
from stt import SAMPLE_RATE as STT_SAMPLE_RATE
from stt import Transcriber
from tts import (
    TTS_SAMPLE_RATE,
    PiperClient,
    build_tts_client,
    chunk_text_for_tts,
    log_piper_apple_silicon_hint_if_needed,
    truthy_env,
    warmup_piper_if_needed,
)
from user_profile import (
    apply_setup_fields,
    get_profile_context,
    increment_topic,
    load_profile,
    profile_needs_setup,
    schedule_profile_update,
)

logger = logging.getLogger("jarvis.backend")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

SERVER_NAME = "jarvis-backend"
SERVER_VERSION = "0.6.0"

# Proactive agents + idle detection
LAST_INTERACTION_TS = time.time()
_ws_clients: set[WebSocket] = set()


class HealthResponse(BaseModel):
    status: str
    server: str
    version: str
    whisper_loaded: bool
    memory: str


class ProfileSetupBody(BaseModel):
    name: str
    city: str = ""
    news_topics: list[str] = []


# Module-level singletons. Models are heavy; one per process.
transcriber = Transcriber()
deepseek = DeepSeekClient()
tts_client = build_tts_client()


async def broadcast_proactive_payload(payload: dict[str, Any]) -> None:
    """Fan-out JSON alerts; optionally stream TTS to every connected client."""
    for ws in list(_ws_clients):
        await _safe_send_json(ws, payload)
    if not payload.get("speak"):
        return
    msg = str(payload.get("message") or "").strip()
    nam = str(payload.get("name") or "").strip()
    if payload.get("type") == "reminder" and nam and msg:
        tts_line = f"{nam}. {msg}"
    elif msg:
        tts_line = msg
    elif nam:
        tts_line = nam
    else:
        return
    await broadcast_tts_all(tts_line)


async def broadcast_tts_all(text: str) -> None:
    """Stream TTS (Piper or ElevenLabs) to all sockets (proactive briefings)."""
    cleaned = _clean_for_tts(text)
    if not cleaned:
        return
    for ws in list(_ws_clients):
        started = False
        for sub in chunk_text_for_tts(cleaned):
            async for pcm in tts_client.stream_pcm(sub):
                if not pcm:
                    continue
                if not started:
                    await _safe_send_json(
                        ws,
                        {"type": "speaking_start", "sample_rate": TTS_SAMPLE_RATE},
                    )
                    started = True
                await ws.send_bytes(pcm)
        if started:
            await _safe_send_json(ws, {"type": "speaking_end"})


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks. Load Whisper here so the first user request is fast."""
    logger.info("JARVIS backend starting (v%s)", SERVER_VERSION)
    logger.info("TTS engine: %s", type(tts_client).__name__)
    log_piper_apple_silicon_hint_if_needed()
    # Piper warmup blocks startup; x86 ONNX under Rosetta can hang for a long time. Opt-in only.
    if truthy_env("PIPER_WARMUP_ON_START"):
        await warmup_piper_if_needed(tts_client)
    elif isinstance(tts_client, PiperClient):
        logger.info(
            "Piper startup warmup skipped — server will boot immediately "
            "(set PIPER_WARMUP_ON_START=1 to prime Piper before first turn). "
            "First TTS subprocess may still be slow or stuck on Intel/Rosetta."
        )
    try:
        await transcriber.warmup()
    except Exception:  # noqa: BLE001
        logger.exception("Whisper warmup failed; first transcription will retry")
    try:
        mem = get_memory()
        if mem.enabled:
            await mem.warmup()
    except Exception:  # noqa: BLE001
        logger.exception("Memory warmup failed; first turn may stall on embedding download")
    configure_tools(broadcast_fn=broadcast_proactive_payload)
    try:
        _mcp_config = os.path.join(os.path.dirname(__file__), "mcp_servers.json")
        await mcp_loader.start(_mcp_config)
    except Exception:  # noqa: BLE001
        logger.exception("MCP loader failed to start")
    configure_proactive(
        broadcast_json=broadcast_proactive_payload,
        deepseek=deepseek,
        last_interaction_ts=lambda: LAST_INTERACTION_TS,
    )
    try:
        start_scheduler()
    except Exception:  # noqa: BLE001
        logger.exception("Proactive scheduler failed to start")
    try:
        start_log_watcher(broadcast_json=broadcast_proactive_payload)
    except Exception:
        logger.exception("Log watcher failed to start")
    yield
    shutdown_log_watcher()
    shutdown_scheduler()
    await mcp_loader.stop()
    logger.info("JARVIS backend shutting down")


app = FastAPI(
    title="JARVIS Backend",
    version=SERVER_VERSION,
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/tools")
async def list_tools() -> dict:
    from tools.registry import REGISTRY
    names = sorted(REGISTRY.known_names())
    return {
        "total": len(names),
        "mcp_servers": mcp_loader.connected_servers(),
        "tools": names,
    }


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(
        status="ok",
        server=SERVER_NAME,
        version=SERVER_VERSION,
        whisper_loaded=transcriber._model is not None,  # noqa: SLF001 (intentional)
        memory=describe_memory_status(),
    )


@app.get("/profile/status")
async def profile_status() -> dict[str, Any]:
    """Return whether the first-launch profile wizard should run."""
    p = load_profile()
    return {"needs_setup": profile_needs_setup(), "name": p.name}


@app.post("/profile/setup")
async def profile_setup(body: ProfileSetupBody) -> dict[str, bool]:
    """Save one-time profile fields from the desktop/web setup overlay."""
    apply_setup_fields(
        name=body.name,
        city=body.city,
        news_topics=body.news_topics,
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Per-connection session
# ---------------------------------------------------------------------------


class Session:
    """In-memory state for a single WS connection.

    Holds the in-flight PCM buffer (Int16 LE) and the current sample rate
    declared by the client at utterance_start time.
    """

    def __init__(self) -> None:
        self.recording: bool = False
        self.sample_rate: int = STT_SAMPLE_RATE
        self.pcm_chunks: list[bytes] = []
        # Conversation history kept across utterances within a single WS
        # session. Phase 5 will inject relevant memories from ChromaDB on
        # top of this; for now we just keep a rolling window.
        self.history: list[ChatMessage] = []
        # Tracks the currently-running utterance handler so we can cancel.
        self.active_task: asyncio.Task[None] | None = None
        self.turn_count: int = 0
        self.session_topics: list[str] = []
        self.short_streak: int = 0

    def reset_recording(self) -> None:
        self.recording = False
        self.pcm_chunks = []
        self.sample_rate = STT_SAMPLE_RATE


# ---------------------------------------------------------------------------
# WebSocket endpoint
# ---------------------------------------------------------------------------


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    global LAST_INTERACTION_TS
    await websocket.accept()
    _ws_clients.add(websocket)
    LAST_INTERACTION_TS = time.time()
    client = websocket.client
    logger.info("WS connected: %s", client)

    session = Session()
    await _send_json(
        websocket,
        {
            "type": "hello",
            "server": SERVER_NAME,
            "version": SERVER_VERSION,
            "needs_profile_setup": profile_needs_setup(),
        },
    )

    try:
        while True:
            message = await websocket.receive()
            # Starlette/FastAPI may deliver text or bytes per frame.
            if message.get("type") == "websocket.disconnect":
                break
            if (text := message.get("text")) is not None:
                await _handle_text(websocket, session, cast(str, text))
            elif (data := message.get("bytes")) is not None:
                _handle_binary(session, cast(bytes, data))
    except WebSocketDisconnect:
        logger.info("WS disconnected: %s", client)
    except Exception:  # noqa: BLE001
        logger.exception("WS handler crashed for %s", client)
        await _safe_send_error(websocket, "internal server error")
        try:
            await websocket.close(code=1011)
        except RuntimeError:
            pass
    finally:
        _ws_clients.discard(websocket)
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()


def _handle_binary(session: Session, data: bytes) -> None:
    if not session.recording:
        return  # ignore stray frames between utterances
    session.pcm_chunks.append(data)


async def _handle_text(websocket: WebSocket, session: Session, raw: str) -> None:
    try:
        msg: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError:
        await _send_json(websocket, {"type": "error", "message": "invalid json"})
        return

    msg_type = msg.get("type")

    if msg_type == "ping":
        await _send_json(websocket, {"type": "pong", "ts": int(msg.get("ts", 0))})
        return
    if msg_type == "echo":
        await _send_json(websocket, {"type": "echo", "text": str(msg.get("text", ""))})
        return

    if msg_type == "utterance_start":
        # If a previous turn is still running, cancel it before recording new audio.
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
        session.reset_recording()
        session.recording = True
        rate = int(msg.get("sample_rate", STT_SAMPLE_RATE))
        if rate < 8000 or rate > 48000:
            await _send_json(
                websocket,
                {"type": "error", "message": f"unsupported sample_rate {rate}"},
            )
            session.recording = False
            return
        session.sample_rate = rate
        return

    if msg_type == "utterance_end":
        if not session.recording:
            return
        session.recording = False
        pcm_bytes = b"".join(session.pcm_chunks)
        sr = session.sample_rate
        session.pcm_chunks = []
        # Run the (long) STT/LLM/TTS pipeline as a background task so the
        # WS receive loop keeps draining client frames (e.g., a cancel).
        session.active_task = asyncio.create_task(
            _run_turn(websocket, session, pcm_bytes, sr)
        )
        return

    if msg_type == "cancel":
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
        session.reset_recording()
        return

    if msg_type == "text_input":
        text = str(msg.get("text", "")).strip()
        if not text:
            return
        if session.active_task and not session.active_task.done():
            session.active_task.cancel()
        session.reset_recording()
        session.active_task = asyncio.create_task(
            _run_text_turn(websocket, session, text)
        )
        return

    await _send_json(
        websocket, {"type": "error", "message": f"unknown message type: {msg_type!r}"}
    )


# ---------------------------------------------------------------------------
# Fast-path routing — skip ReAct for queries that don't need tools
# ---------------------------------------------------------------------------

# Keywords that indicate real-time data is needed (web_search / calendar / files).
# Everything else goes straight to streaming — no decision round-trip.
_TOOL_KEYWORDS: frozenset[str] = frozenset({
    # web search / real-time data
    "weather", "temperature", "forecast", "rain", "humidity", "wind",
    "news", "headline", "headlines",
    "search for", "look up", "find out",
    "stock", "price", "exchange rate", "score", "match result",
    # calendar / files
    "calendar", "schedule", "meeting", "appointment",
    "open file", "read file", "list files", "show files",
    "take a note", "write this down", "create a note", "save note",
    "search my notes", "search notes", "find note", "read note",
    "in my notes", "my notes", "notes app", "check my notes",
    # reminders / alarms / timers / stopwatch
    "remind me", "reminder", "set a timer", "timer",
    "set alarm", "set an alarm", "alarm", "wake me",
    "list alarms", "show alarms", "what alarms",
    "cancel alarm", "delete alarm", "remove alarm",
    "stopwatch", "start stopwatch", "stop stopwatch", "lap time",
    # system / app control
    "open ", "launch ", "start ",
    "volume", "mute", "unmute", "brightness",
    "screenshot", "what's on my screen", "what is on my screen",
    "look at", "read my screen", "see my screen", "my screen",
    "clipboard", "copy to clipboard", "paste",
    # browser control
    "go to", "navigate to", "open website", "open the website",
    "search on youtube", "search youtube", "youtube",
    "google ", "search for ", "look up ",
    "what tab", "current tab", "active tab", "what site",
    "new tab", "close tab", "go back", "go forward", "reload", "refresh",
    "scroll down", "scroll up",
})


def _needs_tools(user_text: str) -> bool:
    lower = user_text.lower()
    return any(kw in lower for kw in _TOOL_KEYWORDS)


def _maybe_early_flush(buf: str, threshold: int = 90) -> tuple[str, str]:
    """Flush buf at a natural word boundary when it's long enough.

    Threshold raised to 90 so Piper receives phrase-length input and
    produces natural prosody rather than choppy sentence fragments.
    Cuts at the last space so we never break inside a word.
    Returns (chunk_to_flush, remaining_buf).  Returns ("", buf) if too short.
    """
    if len(buf) < threshold:
        return "", buf
    window = buf[: threshold + 20]
    cut = window.rfind(" ")
    if cut < 16:
        return "", buf
    return buf[:cut].strip(), buf[cut:].lstrip()


# ---------------------------------------------------------------------------
# Acknowledgment phrases — played immediately after STT while LLM processes
# ---------------------------------------------------------------------------

_ACK_GENERAL = [
    "One moment, sir.",
    "Right away, sir.",
    "Certainly, sir.",
    "Of course, sir.",
    "On it.",
    "Stand by.",
]

_ACK_SEARCH = [
    "Let me look into that, sir.",
    "I'll check on that.",
    "Searching now.",
    "Give me just a moment.",
    "Looking that up for you.",
]

_ACK_THINKING = [
    "Let me think on that, sir.",
    "Good question. One moment.",
    "Considering that now.",
]

_SEARCH_KEYWORDS = {
    "weather", "search", "find", "look up", "lookup", "what is", "what are",
    "who is", "who are", "when is", "where is", "how do", "how does",
    "news", "latest", "today", "current", "price", "score",
}

_THINK_KEYWORDS = {
    "explain", "why", "how", "difference", "compare", "opinion",
    "think", "suggest", "recommend", "advice", "should i",
}


def _pick_acknowledgment(user_text: str) -> str:
    lower = user_text.lower()
    if any(kw in lower for kw in _SEARCH_KEYWORDS):
        return random.choice(_ACK_SEARCH)
    if any(kw in lower for kw in _THINK_KEYWORDS):
        return random.choice(_ACK_THINKING)
    return random.choice(_ACK_GENERAL)


# ---------------------------------------------------------------------------
# Turn orchestration: STT -> LLM -> TTS
# ---------------------------------------------------------------------------


async def _build_system(session: "Session", user_text: str) -> str:
    """Build the full system prompt for one turn (shared by voice and text paths)."""
    prof = load_profile()
    mem = get_memory()
    memory_ctx = await mem.recall(user_text)
    situ_block = build_full_context(
        user_text,
        {
            "turn_count": session.turn_count,
            "topics": session.session_topics[-16:],
            "short_streak": session.short_streak,
        },
        prof,
        memory_ctx,
    )
    return JARVIS_SYSTEM_PROMPT + "\n\n" + get_profile_context() + "\n\n" + situ_block


async def _run_text_turn(websocket: WebSocket, session: "Session", user_text: str) -> None:
    """Silent-command path: skip STT entirely, run LLM+TTS with typed text."""
    global LAST_INTERACTION_TS
    speaking_started = False
    try:
        await _send_json(websocket, {"type": "thinking"})
        await _send_json(websocket, {"type": "transcript", "text": user_text})
        logger.info("text_input: %s", user_text)

        words = user_text.split()
        session.turn_count += 1
        if words:
            session.session_topics.append(words[0].lower())

        full_system = await _build_system(session, user_text)
        use_tools = _needs_tools(user_text)
        full_reply = ""
        sentence_buf = ""
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

        async def produce() -> None:
            nonlocal sentence_buf, full_reply
            if use_tools:
                await tts_queue.put(_pick_acknowledgment(user_text))
                token_stream = run_react_agent(
                    client=deepseek,
                    full_system=full_system,
                    user_message=user_text,
                    history_messages=_history_dicts_only(session.history),
                    on_thinking_step=lambda label: _safe_send_json(
                        websocket, {"type": "thinking_step", "label": label}
                    ),
                )
            else:
                messages = _dict_messages_for_llm(full_system, _trim_history(session.history))
                messages.append({"role": "user", "content": user_text})
                token_stream = deepseek.stream_chat_messages(messages)

            async for delta in token_stream:
                full_reply += delta
                await _send_json(websocket, {"type": "token", "text": delta})
                sentence_buf += delta
                flushable = _drain_sentences(sentence_buf)
                if flushable:
                    for chunk in flushable[:-1]:
                        c = chunk.strip()
                        if c:
                            await tts_queue.put(c)
                    sentence_buf = flushable[-1]
                early, sentence_buf = _maybe_early_flush(sentence_buf)
                if early:
                    await tts_queue.put(early)
            tail = sentence_buf.strip()
            if tail:
                await tts_queue.put(tail)
            await tts_queue.put(None)

        async def consume() -> None:
            nonlocal speaking_started
            while True:
                item = await tts_queue.get()
                if item is None:
                    break
                speaking_started = await _stream_tts_with_lazy_start(
                    websocket, item, lazy_started=speaking_started
                )

        await asyncio.gather(produce(), consume())

        if full_reply.strip():
            await _send_json(websocket, {"type": "final", "text": full_reply.strip()})

        session.history.append(ChatMessage(role="user", content=user_text))
        session.history.append(ChatMessage(role="assistant", content=full_reply.strip()))
        session.history = _trim_history(session.history)
        LAST_INTERACTION_TS = time.time()

        mem = get_memory()
        if mem.enabled and full_reply.strip():
            asyncio.create_task(mem.remember(f"User: {user_text}\nAssistant: {full_reply.strip()}"))

    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("_run_text_turn failed")
        await _safe_send_json(websocket, {"type": "error", "message": "Text turn failed."})
    finally:
        if speaking_started:
            await _safe_send_json(websocket, {"type": "speaking_end"})


async def _run_turn(
    websocket: WebSocket, session: Session, pcm_bytes: bytes, sample_rate: int
) -> None:
    """Run one full STT -> LLM -> TTS turn.

    Protocol invariants:
      - `speaking_start` is sent at most once, lazily, when the first TTS
        byte is ready to ship. It is never sent for failed turns that
        produced no audio.
      - `speaking_end` is sent exactly once iff `speaking_start` was sent.
      - `final` carries the full LLM reply once token streaming completes.
      - `error` is sent on any unexpected failure; clients should treat
        either `speaking_end` or `error` as "turn over".
    """
    global LAST_INTERACTION_TS
    speaking_started = False

    try:
        await _send_json(websocket, {"type": "thinking"})

        # 1) Decode incoming Int16 PCM into Float32 mono and resample to 16 kHz.
        audio = _pcm_int16_to_f32(pcm_bytes)
        if sample_rate != STT_SAMPLE_RATE:
            audio = _resample_linear(audio, sample_rate, STT_SAMPLE_RATE)

        # 2) Speech-to-text.
        result = await transcriber.transcribe(audio)
        user_text = result.text.strip()
        if not user_text:
            # No speech was captured. Ship a graceful response and bail.
            await _send_json(websocket, {"type": "transcript", "text": ""})
            recovery = "I didn't catch that, sir."
            await _send_json(websocket, {"type": "final", "text": recovery})
            speaking_started = await _stream_tts_with_lazy_start(
                websocket, recovery, lazy_started=False
            )
            return

        await _send_json(websocket, {"type": "transcript", "text": user_text})
        logger.info("user: %s", user_text)

        words = user_text.split()
        if len(words) < 5:
            session.short_streak += 1
        else:
            session.short_streak = 0
        session.turn_count += 1
        if words:
            session.session_topics.append(words[0].lower())

        # 3) ReAct agent + streaming TTS (tools executed inside agent loop).
        full_system = await _build_system(session, user_text)

        # history_msgs = previous turns only; current user_text is passed explicitly
        # so cancelled turns don't leave orphaned user messages in history.
        history_msgs = _history_dicts_only(session.history)
        full_reply = ""
        sentence_buf = ""
        tts_queue: asyncio.Queue[str | None] = asyncio.Queue()

        # Fast path: conversational queries skip the ReAct decision round-trip
        # and stream directly — saves ~500ms-1s per turn.
        # Tool queries (weather, search, calendar, files) keep the ReAct loop
        # and get an acknowledgment phrase to cover the extra latency.
        use_tools = _needs_tools(user_text)
        if use_tools:
            await tts_queue.put(_pick_acknowledgment(user_text))

        async def on_thinking_step(label: str) -> None:
            await _send_json(websocket, {"type": "thinking_step", "label": label})

        async def produce() -> None:
            nonlocal sentence_buf, full_reply
            try:
                if use_tools:
                    token_stream: AsyncIterator[str] = run_react_agent(
                        client=deepseek,
                        full_system=full_system,
                        user_message=user_text,
                        history_messages=history_msgs,
                        on_thinking_step=on_thinking_step,
                    )
                else:
                    direct_msgs: list[dict[str, Any]] = [
                        {"role": "system", "content": full_system},
                        *history_msgs,
                        {"role": "user", "content": user_text},
                    ]
                    token_stream = deepseek.stream_chat_messages(
                        direct_msgs, temperature=0.65, max_tokens=512
                    )

                async for delta in token_stream:
                    full_reply += delta
                    sentence_buf += delta
                    await _send_json(websocket, {"type": "token", "text": delta})
                    # Hard sentence boundaries first
                    flushable = _drain_sentences(sentence_buf)
                    if flushable:
                        for chunk in flushable[:-1]:
                            c = chunk.strip()
                            if c:
                                await tts_queue.put(c)
                        sentence_buf = flushable[-1]
                    # Soft flush: fire TTS after ~38 chars without waiting for punctuation
                    early, sentence_buf = _maybe_early_flush(sentence_buf)
                    if early:
                        await tts_queue.put(early)

                tail = sentence_buf.strip()
                if tail:
                    await tts_queue.put(tail)
                await _send_json(websocket, {"type": "final", "text": full_reply})
            finally:
                await tts_queue.put(None)

        async def consume() -> None:
            nonlocal speaking_started
            while True:
                item = await tts_queue.get()
                if item is None:
                    return
                speaking_started = await _stream_tts_with_lazy_start(
                    websocket, item, lazy_started=speaking_started
                )

        await asyncio.gather(produce(), consume())

        # If the UI streamed tokens/final text but nothing ever hit the speaker
        # (broken Piper/rosetta setup, malformed queue chunks, etc.), retry once from
        # the full assembled reply so lazy ``speaking_start`` + PCM cannot be skipped by
        # an edge case while still guaranteeing one synthesis path when text exists.
        if full_reply.strip() and not speaking_started:
            logger.warning(
                "TTS produced no audio for this turn; retrying synthesis from "
                "full reply (%d chars)",
                len(full_reply.strip()),
            )
            speaking_started = await _stream_tts_with_lazy_start(
                websocket,
                full_reply,
                lazy_started=False,
            )

        session.history.append(ChatMessage(role="user", content=user_text))
        session.history.append(ChatMessage(role="assistant", content=full_reply))
        session.history = _trim_history(session.history)
        logger.info("jarvis: %s", full_reply)
        LAST_INTERACTION_TS = time.time()
        increment_topic(" ".join(user_text.split()[:5]).lower())
        schedule_profile_update(user_text, full_reply, deepseek)
        mem = get_memory()
        if mem.enabled and full_reply.strip():
            asyncio.create_task(mem.remember(f"User: {user_text}\nAssistant: {full_reply.strip()}"))

    except asyncio.CancelledError:
        logger.info("turn cancelled")
        raise
    except Exception as exc:  # noqa: BLE001
        logger.exception("turn failed")
        await _safe_send_error(websocket, f"{type(exc).__name__}: {exc}")
    finally:
        if speaking_started:
            await _safe_send_json(websocket, {"type": "speaking_end"})


# ---------------------------------------------------------------------------
# Audio helpers
# ---------------------------------------------------------------------------


def _pcm_int16_to_f32(pcm: bytes) -> np.ndarray:
    """Reinterpret little-endian Int16 bytes as a Float32 numpy array in [-1, 1]."""
    if not pcm:
        return np.zeros(0, dtype=np.float32)
    arr = np.frombuffer(pcm, dtype="<i2").astype(np.float32)
    arr /= 32768.0
    return arr


def _resample_linear(audio: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear interpolation resampler.

    Whisper is robust to mild aliasing, and the input has already passed
    through the browser's anti-aliased microphone resampling (we ask for a
    16 kHz AudioContext on the client side). This is a defensive fallback
    for the rare case the client couldn't honour the rate.
    """
    if src_rate == dst_rate or audio.size == 0:
        return audio
    ratio = dst_rate / src_rate
    n_out = int(round(audio.size * ratio))
    if n_out <= 0:
        return np.zeros(0, dtype=np.float32)
    src_idx = np.linspace(0, audio.size - 1, n_out, dtype=np.float64)
    lo = np.floor(src_idx).astype(np.int64)
    hi = np.minimum(lo + 1, audio.size - 1)
    frac = (src_idx - lo).astype(np.float32)
    return (audio[lo] * (1.0 - frac) + audio[hi] * frac).astype(np.float32)


# ---------------------------------------------------------------------------
# LLM helpers
# ---------------------------------------------------------------------------

_MAX_HISTORY_MESSAGES = 16


def _trim_history(history: list[ChatMessage]) -> list[ChatMessage]:
    return history[-_MAX_HISTORY_MESSAGES:]


def _dict_messages_for_llm(
    system_text: str, history: list[ChatMessage]
) -> list[dict[str, Any]]:
    """OpenAI-style dict messages for DeepSeek (system + user/assistant only)."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system_text}]
    for m in history:
        if m.role not in ("user", "assistant"):
            continue
        out.append({"role": m.role, "content": m.content})
    return out


def _history_dicts_only(history: list[ChatMessage]) -> list[dict[str, Any]]:
    """User/assistant turns only (no system) for the ReAct final stream."""
    return [
        {"role": m.role, "content": m.content}
        for m in _trim_history(history)
        if m.role in ("user", "assistant")
    ]


def _drain_sentences(buf: str) -> list[str]:
    """Split `buf` at sentence/clause boundaries; return parts.

    Last element is the remaining (incomplete) tail — caller keeps it.
    """
    # Split greedily on .!? followed by whitespace.
    out: list[str] = []
    start = 0
    i = 0
    while i < len(buf):
        ch = buf[i]
        if ch in ".!?":
            j = i + 1
            while j < len(buf) and buf[j].isspace():
                j += 1
            if j > i + 1 or j == len(buf):
                out.append(buf[start:j].strip())
                start = j
                i = j
                continue
        i += 1
    out.append(buf[start:])
    return [s for s in out if s != ""]


# ---------------------------------------------------------------------------
# TTS helpers
# ---------------------------------------------------------------------------


_MD_BOLD_ITALIC = re.compile(r"\*{1,2}(.+?)\*{1,2}")
_MD_UNDER       = re.compile(r"_{1,2}(.+?)_{1,2}")
_MD_CODE_INLINE = re.compile(r"`([^`]+)`")
_MD_CODE_BLOCK  = re.compile(r"```[\s\S]*?```")
_MD_LINK        = re.compile(r"\[([^\]]+)\]\([^\)]+\)")
_MD_HEADING     = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MD_BLOCKQUOTE  = re.compile(r"^>\s*", re.MULTILINE)
_MD_LIST        = re.compile(r"^\s*[-*+]\s+", re.MULTILINE)
_MD_OLIST       = re.compile(r"^\s*\d+\.\s+", re.MULTILINE)
_MD_HR          = re.compile(r"^[-*_]{3,}\s*$", re.MULTILINE)
_MULTI_SPACE    = re.compile(r"  +")


def _clean_for_tts(text: str) -> str:
    """Strip markdown and special characters that TTS should not speak aloud."""
    t = _MD_CODE_BLOCK.sub("", text)
    t = _MD_BOLD_ITALIC.sub(r"\1", t)
    t = _MD_UNDER.sub(r"\1", t)
    t = _MD_CODE_INLINE.sub(r"\1", t)
    t = _MD_LINK.sub(r"\1", t)
    t = _MD_HEADING.sub("", t)
    t = _MD_BLOCKQUOTE.sub("", t)
    t = _MD_LIST.sub("", t)
    t = _MD_OLIST.sub("", t)
    t = _MD_HR.sub("", t)
    # Punctuation / symbol substitutions
    t = t.replace("—", ", ").replace("–", " to ")
    t = t.replace("…", "...")
    t = t.replace("◈", "").replace("·", "")
    # Stray markdown chars that survived the above passes
    t = t.replace("*", "").replace("_", " ").replace("#", "").replace("`", "")
    # Collapse whitespace
    t = t.replace("\n", " ")
    t = _MULTI_SPACE.sub(" ", t)
    return t.strip()


async def _stream_tts_with_lazy_start(
    websocket: WebSocket, text: str, *, lazy_started: bool
) -> bool:
    """Stream TTS audio for `text`, sending `speaking_start` only the first
    time we have an actual byte of audio to ship.

    Returns the new `speaking_started` value (True once the lazy header has
    been emitted). Caller should pass it back in subsequent calls so the
    header is only sent once across many TTS chunks.
    """
    started = lazy_started
    cleaned = _clean_for_tts(text)
    if not cleaned:
        return started
    for sub in chunk_text_for_tts(cleaned):
        async for pcm in tts_client.stream_pcm(sub):
            if not pcm:
                continue
            if not started:
                await _send_json(
                    websocket,
                    {"type": "speaking_start", "sample_rate": TTS_SAMPLE_RATE},
                )
                started = True
            await websocket.send_bytes(pcm)
    return started


# ---------------------------------------------------------------------------
# WS send helpers
# ---------------------------------------------------------------------------


async def _send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    await websocket.send_text(json.dumps(payload))


async def _safe_send_json(websocket: WebSocket, payload: dict[str, Any]) -> None:
    try:
        await _send_json(websocket, payload)
    except Exception:  # noqa: BLE001
        pass


async def _safe_send_error(websocket: WebSocket, message: str) -> None:
    await _safe_send_json(websocket, {"type": "error", "message": message})
