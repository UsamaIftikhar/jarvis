# JARVIS

Voice-controlled AI assistant desktop app inspired by Tony Stark's JARVIS.

## Stack

- **Frontend**: Next.js 14 (App Router) · TypeScript (strict) · Tailwind · Three.js · Zustand
- **Backend**: Python 3.11 · FastAPI · WebSockets
- **STT**: faster-whisper (local, CPU)
- **LLM**: DeepSeek V3 Flash (streaming)
- **TTS**: ElevenLabs (streaming)
- **Wake word**: Picovoice Porcupine ("Hey JARVIS") + custom double-clap detector
- **Memory**: ChromaDB
- **Desktop**: Tauri v2

## Layout

```
jarvis/
├── frontend/          Next.js 14 + TS HUD (runs in `next dev` and is bundled by Tauri)
├── backend/           FastAPI WebSocket server (uvicorn main:app)
└── desktop/           Tauri shell config (Phase 6)
```

## Phase status

- [x] **Phase 1** — Next.js shell + FastAPI WebSocket echo
- [ ] **Phase 2** — Double-clap detection + state machine
- [ ] **Phase 3** — Full voice pipeline (mic → STT → LLM → TTS → speakers)
- [ ] **Phase 4** — Three.js animation states (LISTENING / THINKING / SPEAKING)
- [ ] **Phase 5** — Tools (web/fs/calendar) + ChromaDB memory
- [ ] **Phase 6** — Tauri packaging

## Running locally (Phase 1)

```bash
# Terminal A — backend (creates a Python 3.11 venv on first run)
cd backend
uv sync
uv run uvicorn main:app --reload --port 8000

# Terminal B — frontend
cd frontend
pnpm install
pnpm dev          # http://localhost:3000
```

## Environment variables

Copy `backend/.env.example` to `backend/.env`. Phase 1 doesn't need any keys.
We'll fill `DEEPSEEK_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`,
and Picovoice credentials in Phase 3.
