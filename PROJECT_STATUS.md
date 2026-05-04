# JARVIS — project status & architecture

This document captures the product intent, stack, major modules, tool surface, environment hooks, and notable implementation history for the JARVIS AI assistant.

---

## What JARVIS is

A **voice-oriented AI assistant** (Iron Man–style “J.A.R.V.I.S.”) with a **futuristic HUD** (Next.js + Three.js), a **Python backend** (FastAPI + WebSockets), **local speech-to-text**, **streaming LLM** (DeepSeek), **TTS** (Piper / ElevenLabs), optional **ChromaDB memory**, **tools** (web, filesystem sandbox, calendar `.ics`, macOS integration), and an optional **Tauri desktop** shell in the repo layout.

---

## Architecture (backend)

- **`main.py`** — FastAPI app, WebSocket protocol (mic PCM in, TTS PCM out, JSON control frames: transcript, tokens, final reply, thinking steps, errors, proactive alerts, **reminder** payloads, etc.).
- **`llm.py`** — DeepSeek streaming client + **`JARVIS_SYSTEM_PROMPT`** (brevity, tool rules, personality, “sir”, and guidance such as Clock weekday alarms vs one-shot wording).
- **`agent_loop.py`** — ReAct-style loop with **`TOOL_HELP`**, JSON tool actions, terminal tools, streamed final answers.
- **`tools.py`** — OpenAI-style tool schemas + implementations: search, FS (under `JARVIS_FS_ROOT`), calendar, **alarms**, **reminders**, volume/brightness, clipboard, screenshot (Anthropic vision), browser helpers, stopwatch, etc.
- **`user_profile.py` / `situational.py` / `proactive_agent.py`** — Profile memory, situational context, scheduled proactive jobs (with scheduler).
- **`memory`/Chroma** — Long-term embeddings store (`JARVIS_CHROMA_PATH`).

---

## Frontend

- **Next.js 14** App Router, **Zustand** (`state.ts`), **WebSocket client** (`wsClient.ts`) with typed server messages.
- **Voice pipeline** (`voicePipeline.ts`) — STT → agent → TTS playback.
- **HUD** — `JarvisCanvas`, `StatusOverlay` (state badge, transcript, streaming reply, **proactive alerts**, optional **title** + body for alerts/reminders), wake/clap/mic UI.
- **`ProfileSetup`**, **`SilentCommand`**, etc.

---

## Tools & integrations (capability overview)

| Area | Behavior |
|------|----------|
| **Voice turn** | Utterance → Whisper → LLM → streamed tokens + TTS audio over WS |
| **Web** | Tavily search |
| **Workspace FS** | List/read files under sandbox root |
| **Calendar** | Read upcoming events from a linked `.ics` (`JARVIS_CALENDAR_ICS`) |
| **Alarms** | Writes macOS **Clock** (`mobiletimerd` defaults), restart daemon so UI updates |
| **Cancel alarm** | Edit plist + daemon restart; optional **delete all** and weekday-aware matching |
| **Reminders (`set_reminder`)** | Delayed **in-session** HUD + TTS via WS; **also** creates **Apple Reminders** when possible (AppleScript) so items appear in the system app |
| **Machine control** | Open apps, volume/mute, brightness, clipboard |
| **Browser** | Open URL, search, tab actions (helpers tied to browser automation) |
| **Screenshot** | Optional vision via Anthropic (needs API key) |
| **Stopwatch** | In-memory timer |

Environment knobs include **`JARVIS_TIMEZONE`** (alarm wall-clock alignment when process TZ ≠ Mac), **`JARVIS_FS_ROOT`**, **`JARVIS_CHROMA_PATH`**, Whisper/TTS keys, etc.

---

## Session / iteration highlights (implementation history)

Notable behaviors addressed over development iterations:

1. **Clock alarms — weekday bitmask** — Apple uses **Monday-first** repeat bits; decoding aligned so weekdays match **Clock** (e.g. Monday vs Tuesday).
2. **Repeat parsing** — e.g. **`repeats`** vs **`repeat`** in regex; tool args must carry repeat text in **`time`** so bits are not dropped.
3. **Local time for alarms** — **`_local_now()`** / optional **`JARVIS_TIMEZONE`** to reduce UTC vs local hour bugs.
4. **Parsing** — Stronger clock extraction so long phrases don’t mis-parse times.
5. **Cancel alarm** — Restart **`mobiletimerd`** after plist edits; optional **repeat-schedule** match when weekdays appear in cancel text; **`time` + `label`** merged; **delete all alarms**.
6. **Voicecopy for weekday alarms** — Responses clarify Apple **Clock** uses **weekly** weekday alarms when a day is named (not a true one-off date).
7. **Reminders vs alarms** — **Alarm fallback** to Apple Reminders only when **Clock write fails**; **`set_reminder`** gained **native Reminders** creation for reliability + notifications (alongside in-session HUD).
8. **`set_reminder`** — Optional **`name`**, **`message`** optional if name set; **`reminder`** WS type handled in UI; **TTS** for reminder payloads in **`broadcast_proactive_payload`**; HUD shows optional **title**.
9. **Guardrail** — **`.cursor/rules/do-not-touch-alarms.mdc`** — **Do not change alarm logic** unless explicitly requested.

---

## Repo layout (from README)

- **`frontend/`** — Next HUD
- **`backend/`** — FastAPI (`uvicorn main:app`)
- **`desktop/`** — Tauri wrapper

README phase checkboxes may lag the codebase; many Phase 3–5 pieces are already present in code.

---

## How to run (typical)

- Backend: `cd backend` → venv / `uv run` → **`uvicorn main:app`** (port from your setup).
- Frontend: `cd frontend` → **`pnpm dev`**.
- Configure **`backend/.env`** (DeepSeek, optional ElevenLabs/Piper, Tavily, paths, etc.).

---

## Alarm code policy

Alarm-related paths are treated as **frozen** unless the user explicitly asks to change alarm behavior. See **`.cursor/rules/do-not-touch-alarms.mdc`**.
