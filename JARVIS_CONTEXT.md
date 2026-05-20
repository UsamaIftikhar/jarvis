# JARVIS — Full Project Context for Claude

Use this document to give Claude complete context before any coding session on this project.

---

## What Is JARVIS

An Iron Man–style personal AI assistant. Voice-first — you speak to it, it speaks back. Has a futuristic HUD frontend, runs on a Mac, connects to an iPhone via a native iOS app. Think: always-on executive assistant that has access to your calendar, email, files, GitHub, Google Drive, and can proactively alert you to things.

**Current state:** Fully working end-to-end. Voice in → STT → LLM (DeepSeek) → ReAct tool loop → TTS out. Gmail, Google Drive, GitHub, calendar, alarms, reminders, web search — all wired. Marketing sub-system for a separate e-commerce brand (Khas Bazaar) is also integrated.

---

## Tech Stack

| Layer | Technology |
|---|---|
| LLM | DeepSeek V3 (`deepseek-chat`) via OpenAI-compatible API |
| STT | OpenAI Whisper (local, runs via `faster-whisper`) |
| TTS | Piper (local, `en_US-lessac-medium.onnx`) or ElevenLabs (cloud fallback) |
| Backend | Python 3.12, FastAPI, WebSockets, APScheduler |
| Package manager | `uv` (not pip) |
| Frontend | Next.js 14 (App Router), TypeScript, Tailwind, Three.js |
| iOS app | Capacitor wrapper around the Next.js app |
| Memory | ChromaDB + `sentence-transformers/all-MiniLM-L6-v2` |
| External tools | MCP (Model Context Protocol) for Gmail |
| Marketing AI | Google Vertex AI (Imagen 3, Veo 2) + Meta Graph API |

---

## Repository Layout

```
jarvis/
├── backend/                 Python FastAPI backend
│   ├── main.py              Entrypoint — FastAPI app, WebSocket protocol
│   ├── agent_loop.py        ReAct agent loop
│   ├── llm.py               DeepSeek client + JARVIS system prompt
│   ├── tts.py               Piper / ElevenLabs / SayClient TTS
│   ├── stt.py               Whisper transcription
│   ├── memory.py            ChromaDB long-term memory
│   ├── user_profile.py      Persistent user profile (JSON)
│   ├── situational.py       Time/sentiment context injected per turn
│   ├── proactive_agent.py   APScheduler background jobs
│   ├── conversation_log.py  Persistent turn history (JSONL)
│   ├── watcher.py           File watcher for hot reload
│   ├── mcp_servers.json     MCP server configuration
│   ├── tools/               Tool registry + all built-in tools
│   │   ├── registry.py      ToolEntry dataclass + Registry class
│   │   ├── mcp_loader.py    MCP client (spawns child processes)
│   │   ├── alarms.py        macOS Clock alarms
│   │   ├── calendar_tool.py .ics calendar reader
│   │   ├── filesystem.py    Sandboxed file read/list
│   │   ├── gdrive.py        Google Drive (search/read/upload/delete)
│   │   ├── github_tools.py  GitHub REST API + AI PR review
│   │   ├── morning_brief.py Daily brief (calendar + weather + email)
│   │   ├── notes.py         macOS Notes via AppleScript
│   │   ├── reminders.py     Apple Reminders + in-session HUD alerts
│   │   ├── stopwatch.py     In-memory timer
│   │   ├── system.py        Volume, brightness, clipboard, open apps
│   │   ├── vision.py        Screenshot analysis (Anthropic API)
│   │   └── web.py           Tavily web search + browser open
│   ├── marketing/           Khas Bazaar marketing sub-system
│   │   ├── orchestrator.py  Router → classifies intent → dispatches agent
│   │   ├── agents/          content, social, analytics, strategy, base
│   │   └── tools/           catalog, content, meta, vertex, gemini tools
│   └── data/                Runtime data (profiles, chroma, logs, state)
├── frontend/                Next.js HUD
│   ├── app/page.tsx         Root page, WebSocket lifecycle
│   ├── components/          HUD components
│   └── lib/                 Voice pipeline, WS client, state store
└── frontend/ios/            Capacitor Xcode project
```

---

## Backend Architecture — Layer by Layer

### Layer 1: WebSocket Protocol (`main.py`)

All communication between frontend and backend happens over a single WebSocket connection. Text frames are JSON; binary frames are raw Int16 LE PCM audio.

**Client → Server:**
- `{"type":"utterance_start","sample_rate":int}` — begin streaming mic audio
- `{"type":"utterance_end"}` — commit audio, trigger processing
- `{"type":"ping","ts":int}` / `{"type":"cancel"}` / `{"type":"echo",...}`
- `BINARY` — Int16 LE PCM mono chunks at declared sample rate

**Server → Client:**
- `{"type":"thinking"}` — processing started
- `{"type":"thinking_step","label":str}` — agent tool step label
- `{"type":"transcript","text":str}` — final STT result
- `{"type":"token","text":str}` — LLM streaming token
- `{"type":"final","text":str}` — full reply
- `{"type":"speaking_start","sample_rate":int}` + BINARY PCM + `{"type":"speaking_end"}`
- `{"type":"proactive_alert","message":str,"priority":str,"speak":bool}`
- `{"type":"reminder","message":str,"name":str}`

### Layer 2: Conversation Turn (`main.py`)

Each turn follows this pipeline:
1. Collect streaming PCM → Whisper transcription
2. `_build_system()` — merges system prompt + user profile + situational context + conversation summary + memory recall
3. Route: marketing request → `run_marketing_agent()`, tool-needing request → `run_react_agent()`, simple → direct LLM stream
4. Token stream is simultaneously: sent to browser (`token` frame) + buffered into sentence chunks for TTS
5. After reply: persist to conversation log, update user profile (async), update ChromaDB memory (async)

**Sentence buffering for TTS:** `_drain_sentences()` splits at `.!?` boundaries (skips `\d\.` to avoid splitting numbered list items), then `_clean_for_tts()` strips markdown, URLs, mid-sentence dashes, bullet symbols, numbered list dots before feeding to Piper.

### Layer 3: ReAct Agent (`agent_loop.py`)

Multi-step reasoning loop (max 6 steps, 30s timeout):

1. `_decide()` — LLM returns `{"thought":..., "action":..., "args":{...}}`
2. Validate action against `REGISTRY.known_names()`
3. Duplicate-call guard: tracks `(action, canonical_args_json)` — same tool with same args is blocked, same tool with different args is allowed (e.g. reading two different emails)
4. Param-error retry: if tool returns a Zod/JSON-schema validation error (missing required field), allows one retry without counting as a duplicate
5. `run_tool(action, json.dumps(args))` — dispatches to registered handler
6. Result appended to `scratchpad`, loop continues
7. After loop: `_build_final_answer()` — LLM synthesises scratchpad into natural speech

**Blocked-call prompt:** Each `_decide()` call receives a `blocked_calls` list showing what's already been called this turn, formatted as `tool(args_json)` so the LLM knows exactly what it cannot repeat.

### Layer 4: Tool System (`tools/`)

**Registry pattern:** `ToolEntry` dataclass holds `{definition, handler, thinking_label, terminal, help_hint}`. All lookups are live (not cached) so MCP tools registered after startup are always visible.

`run_tool(name, args_json)` looks up the registry, deserializes args, calls `handler(args_dict)`, returns string result.

**Terminal tools:** write/send/delete tools (`send_*`, `create_*`, `delete_*`, etc.) are flagged `terminal=True` — the ReAct loop stops after them to prevent double-posting.

**MCP tools:** namespaced as `servername__toolname` (e.g. `gmail__search_emails`). HTML-heavy results (like email bodies) are auto-stripped through `_strip_html()` before being returned to the agent — uses stdlib `html.parser`, skips `<style>/<script>` blocks, adds newlines at block tags. This is critical for foodpanda-style HTML-only emails.

### Layer 5: Memory System

Three memory layers stack:

| Layer | Scope | Where |
|---|---|---|
| In-session history | Current session, last 30 turns | `session.history` (in-memory `ChatMessage` list) |
| Persistent conversation log | Last 30 turns loaded at startup | `data/conversation_log.jsonl` |
| ChromaDB semantic memory | Long-term facts, unlimited | `data/chroma/` via sentence-transformers |

After every turn, `update_from_conversation()` runs an async LLM extraction pass to update `user_profile.json` with new facts, city, projects, people, topic frequency.

Memory recall is injected into the system prompt via `get_memory().recall(text)` — top-k semantic search against past conversations.

---

## User Profile (`user_profile.py`)

Stored at `data/user_profile.json`. Fields:

```json
{
  "name": "...",
  "city": "...",
  "timezone": "Asia/Karachi",
  "preferences": {"news_topics":[], "temp_unit":"C", "response_style":"brief"},
  "current_projects": [{"name", "description", "deadline", "priority"}],
  "people": {"Name": "relationship"},
  "routines": {"morning_start":"08:00", "work_hours":"09:00-18:00", "sleep_time":"23:30"},
  "facts": [...],
  "topic_frequency": {"weather lahore": 5, ...},
  "topic_week_id": "2026-W21"
}
```

`get_profile_context()` returns a compact block injected into every LLM system prompt. `increment_topic()` + `get_repeated_topics()` power the "you've asked about X 3 times" suggestions.

---

## Situational Context (`situational.py`)

Injected into every prompt:
- Current time, day, timezone flags (`late_night`, `work_hours`, `weekend`, etc.)
- Tone guidance ("It is late; be brief and gentle.")
- Session sentiment detection
- Repeated topic hints from profile

---

## Proactive System (`proactive_agent.py`)

APScheduler jobs running in the backend process:

| Job | Schedule | What it does |
|---|---|---|
| `morning_brief_job` | Cron at `morning_start` (default 08:00) | Fetches calendar + weather + emails, LLM summarises into spoken brief. Fires only if client connected. Once-per-day (cached). |
| `smart_email_scan` | Every 30 min (8 AM–10 PM) | Fetches unread emails, LLM judges importance (invoices/payments/real people vs newsletters), alerts if something important found. Tracks seen email IDs in `proactive_state.json` to avoid re-alerting. |
| `meeting_countdown` | Every 5 min | Alerts 15 min before any calendar event. Tracks already-alerted events per day. |
| `weather_watch` | Every 30 min | Alerts on storms, >40% rain probability, or ≥38°C heat. Once per condition per day. |
| `idle_reminder` | Every 3 hrs | If quiet 2+ hours during weekday work hours, fetches next meeting + unread emails and LLM-composes a contextual nudge (not just "anything I can help?"). |
| `evening_wrapup` | Cron at 18:00 | 2-sentence end-of-day summary: remaining events + notable emails. Once per day. |
| `topic_digest` | Cron Sunday 09:00 | Weekly digest for topics asked 5+ times. |
| `refresh_gmail_token` | Every 45 min | Keeps Gmail OAuth access token fresh. |

All jobs are wrapped in `try/except` and check `_clients_connected()` before speaking. Proactive alerts broadcast to all connected WebSocket clients via `broadcast_proactive_payload()`.

---

## TTS (`tts.py`)

**Text cleaning pipeline** (in `_clean_for_tts()`):
1. Strip code blocks, markdown bold/italic/underline/links/headings/blockquotes
2. Strip list markers (`- `, `* `, `1. `) at line starts
3. Convert `—` → `, `, `–` → ` to `
4. Strip `•` bullets, mid-sentence ` - ` dashes → `, `
5. Strip numbered list remnants: `\b\d+\.\s+` → digit only
6. Strip URLs, stray `* _ # \`` chars
7. Collapse whitespace

**Providers:**
- `PiperPythonClient` — default (`JARVIS_TTS_PROVIDER=piper`). Uses `piper-tts` Python package. Model: `en_US-lessac-medium.onnx`. Speed: `PIPER_LENGTH_SCALE=0.9`.
- `PiperClient` — CLI-based (`JARVIS_TTS_PROVIDER=piper-cli`). `--output-raw` streaming. Same model.
- `ElevenLabsClient` — cloud (`JARVIS_TTS_PROVIDER=elevenlabs`). `eleven_flash_v2_5` model.
- `SayClient` — macOS `say` command (`JARVIS_TTS_PROVIDER=say`). Free fallback.

**Streaming:** `chunk_text_for_tts()` splits at sentence boundaries. `speaking_start` frame is sent lazily (only when first PCM byte arrives). Multiple TTS chunks play without gap.

---

## STT (`stt.py`)

Local Whisper via `faster-whisper`. Model size from `WHISPER_MODEL` env var (`tiny` / `base` / `small.en` / `medium` / `large-v3`). Audio is collected as Int16 PCM, resampled to 16 kHz mono, transcribed in one shot on `utterance_end`.

---

## Built-in Tools (25+)

| Tool name | What it does |
|---|---|
| `web_search` | Tavily API search |
| `open_browser` | Open URL or search in browser |
| `calendar_upcoming` | Read events from `.ics` file (next N days) |
| `set_alarm` | Create macOS Clock alarm (weekday bitmask, label) |
| `cancel_alarm` | Cancel alarm by label |
| `delete_all_alarms` | Delete all non-active Clock alarms |
| `list_alarms` | List current Clock alarms |
| `create_reminder` | Create Apple Reminder via AppleScript |
| `list_reminders` | List Apple Reminders |
| `complete_reminder` | Mark reminder done |
| `read_file` / `list_files` | Sandboxed filesystem access under `JARVIS_FS_ROOT` |
| `screenshot_analyze` | Take screenshot + analyze via Anthropic Vision |
| `set_volume` / `get_volume` | macOS system volume |
| `set_brightness` | Screen brightness |
| `get_clipboard` / `set_clipboard` | Clipboard read/write |
| `open_application` | Launch macOS app by name |
| `start_stopwatch` / `stop_stopwatch` / `get_stopwatch` | In-memory timer |
| `create_note` / `read_note` / `list_notes` | macOS Notes via AppleScript |
| `morning_brief` | Once-daily brief: calendar + weather + top emails |
| `gdrive_search` / `gdrive_read_file` / `gdrive_upload_file` / `gdrive_delete_file` | Google Drive full CRUD |
| `github_list_repos` / `github_list_prs` / `github_list_issues` / `github_review_pr` / `github_post_pr_review` | GitHub REST + AI code review |
| `gmail__search_emails` | Search Gmail inbox |
| `gmail__read_email` | Read full email by ID (HTML auto-stripped to text) |
| `gmail__send_email` / `gmail__reply_email` | Send / reply |
| `gmail__list_labels` / `gmail__modify_labels` / `gmail__trash_email` | Label management |

---

## MCP Integration (`tools/mcp_loader.py`)

Reads `backend/mcp_servers.json`. At app startup, spawns each server as a child process via stdio, calls `session.list_tools()`, registers all tools as `servername__toolname`.

Current servers:
```json
[
  {"name": "gmail", "command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"]},
  {"name": "slack", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-slack"],
   "env": {"SLACK_BOT_TOKEN": "${SLACK_BOT_TOKEN}", "SLACK_TEAM_ID": "${SLACK_TEAM_ID}"}}
]
```

**HTML stripping:** `_result_to_str()` in `mcp_loader.py` detects HTML in results (checks for `<html>`, `<table>`, `<div>`, `<span>`) and runs `_strip_html()` — a stdlib `html.parser`-based converter that skips `<style>`/`<script>` blocks and adds newlines at block-level tags. This makes HTML-only emails (like foodpanda receipts) readable by the LLM.

**Credentials:**
- Gmail OAuth: `~/.gmail-mcp/gcp-oauth.keys.json` (GCP OAuth client), `~/.gmail-mcp/credentials.json` (token cache)
- Google Drive: `~/.gdrive-mcp/gcp-oauth.keys.json`, `~/.gdrive-mcp/credentials.json`

---

## Marketing Sub-System (`marketing/`)

A separate AI system embedded inside JARVIS for managing **Khas Bazaar** — a Pakistani home decor e-commerce brand selling vases, stems, pampas grass, decorative items.

**Routing:** `main.py` calls `_needs_marketing(user_text)` — keyword detection for brand/product/content/Instagram mentions. If matched, routes to `run_marketing_agent()` instead of the normal ReAct loop.

**Orchestrator:** LLM classifies the request into one of 5 domains → dispatches to the appropriate agent:

| Domain | Agent | What it handles |
|---|---|---|
| `content` | `ContentAgent` | Captions, hooks, hashtags, Reel briefs, content calendars |
| `social` | `SocialAgent` | Posting to Instagram/Facebook, generating AI images/videos |
| `analytics` | `AnalyticsAgent` | Insights, reach, follower stats, post performance |
| `strategy` | `StrategyAgent` | Weekly review, what to post next, product catalog management |
| `general` | `ContentAgent` | Brand questions, product info |

**Marketing Tools:**
- `vertex_tools.py` — Vertex AI **Imagen 3** (image generation) + **Veo 2** (video generation). Saves to `backend/generated_content/`. Needs `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS`.
- `meta_tools.py` — Meta Graph API for posting to Instagram Business + Facebook Page. Needs `META_PAGE_ID`, `META_IG_USER_ID`, `META_PAGE_ACCESS_TOKEN`.
- `catalog_tools.py` — product catalog CRUD (JSON-backed), reads product descriptions for content generation
- `content_tools.py` — caption writing, hashtag generation, content calendar creation
- `gemini_playwright.py` — Gemini Flash + Playwright for analytics scraping

**Brand aesthetic:** Minimal, earthy, neutral tones. Captions: 1–3 lines max, no cheesy CTA, organic feel. Target audience: Pakistani home decor buyers on Instagram.

---

## Frontend (`frontend/`)

Next.js 14 App Router, TypeScript, Tailwind CSS.

### Key Files

| File | Purpose |
|---|---|
| `app/page.tsx` | Root page — WebSocket lifecycle, mic pipeline, keyboard shortcuts, settings modal trigger |
| `lib/voicePipeline.ts` | Full audio pipeline: `ScriptProcessorNode`/`AudioWorkletNode` → Int16 PCM → WS; incoming PCM frames → `AudioBufferSourceNode` chain for gapless playback |
| `lib/wsClient.ts` | Typed WS client with auto-reconnect |
| `lib/state.ts` | Zustand store — states: `IDLE → LISTENING → THINKING → SPEAKING → ERROR` |
| `lib/jarvisEndpoints.ts` | Backend URL resolution: `localStorage["jarvis_backend_url"]` → `NEXT_PUBLIC_BACKEND_URL` → `localhost:8000` |
| `lib/nativeBridge.ts` | Capacitor bridge to `JarvisWakePlugin` (native iOS wake word) |
| `components/JarvisCanvas.tsx` | Three.js animated orb HUD |
| `components/StatusOverlay.tsx` | State badge, transcript, streaming reply text, proactive alerts |
| `components/WakeDetector.tsx` | Wake word: native path (iOS `JarvisWakePlugin`) or Web Speech API fallback (desktop) |
| `components/BackendSettings.tsx` | Settings gear modal — enter Tailscale IP, tests `/health` before saving |
| `components/ProfileSetup.tsx` | First-launch profile setup overlay |

### State Machine
```
IDLE ──(mic tap / wake word)──→ LISTENING
LISTENING ──(silence / tap)──→ THINKING
THINKING ──(first token)──→ SPEAKING (or stays THINKING if tool loop is long)
SPEAKING ──(speaking_end)──→ IDLE
Any state ──(error)──→ ERROR ──(auto-retry)──→ IDLE
```

---

## iOS App (`frontend/ios/`)

Next.js exported as a static site (`pnpm build:capacitor`) wrapped in a Capacitor iOS app.

### Native Wake Word Plugin (`JarvisWakePlugin.swift`)
- `AVAudioSession.playAndRecord` with `.defaultToSpeaker`
- Silent PCM buffer loop via `AVAudioPlayerNode` — keeps app alive in iOS background (requires `UIBackgroundModes: [audio]` in `Info.plist`)
- `SFSpeechRecognizer` with `requiresOnDeviceRecognition = true` — fully on-device, no internet needed for wake detection
- Recognises "Hey JARVIS" → fires `"wakeWord"` Capacitor event → web layer starts recording
- Auto-restarts after Apple's ~1 min recognition session limit
- Handles audio interruptions (phone calls, Siri)
- `AppDelegate.swift` sets `.defaultToSpeaker` at launch for loudspeaker routing

### Connectivity
- **Tailscale VPN** mesh — Mac and iPhone share `100.x.x.x` IPs, reachable from anywhere
- Backend URL stored in `localStorage["jarvis_backend_url"]` — set in the ⚙ gear modal at runtime
- No rebuild required to switch backend IP

### Build Commands
```bash
pnpm build:capacitor    # Next.js static export with CAPACITOR_BUILD=1
pnpm cap:sync           # Copy out/ into Xcode project
pnpm cap:open           # Open Xcode
# Then ⌘R in Xcode to install on device
```

**Note:** Free Apple ID personal team cert expires every 7 days. Re-run from Xcode to refresh.

---

## Environment Variables (`.env`)

```bash
# LLM
DEEPSEEK_API_KEY=
DEEPSEEK_MODEL=deepseek-chat
DEEPSEEK_BASE_URL=https://api.deepseek.com   # optional override

# TTS
JARVIS_TTS_PROVIDER=piper                    # piper | piper-cli | elevenlabs | say
PIPER_MODEL=data/piper/en_US-lessac-medium.onnx
PIPER_CMD=piper                              # piper-cli only
PIPER_LENGTH_SCALE=0.9                       # < 1.0 = faster speech
PIPER_TIMEOUT_S=120
PIPER_PREFIX_ARGS=arch -x86_64               # Apple Silicon + Rosetta Piper
PIPER_DYLD_LIBRARY_PATH=/usr/local/opt/espeak-ng/lib
ELEVENLABS_API_KEY=
ELEVENLABS_VOICE_ID=
ELEVENLABS_MODEL=eleven_flash_v2_5

# STT
WHISPER_MODEL=small.en                       # tiny | base | small.en | medium | large-v3

# Tools
TAVILY_API_KEY=                              # web search
ANTHROPIC_API_KEY=                           # screenshot vision (optional)
JARVIS_FS_ROOT=                              # sandboxed filesystem root
JARVIS_CALENDAR_ICS=                         # path to .ics calendar file
JARVIS_TIMEZONE=Asia/Karachi                 # TZ for alarm wall-clock alignment
JARVIS_HISTORY_TURNS=30                      # how many turns to load on startup

# Memory
JARVIS_CHROMA_PATH=data/chroma
JARVIS_EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2

# GitHub
GITHUB_TOKEN=                                # personal access token

# Google (Drive + Marketing)
GOOGLE_CLOUD_PROJECT=
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=backend/gcloud-key.json
GEMINI_API_KEY=                              # available, partially wired

# Marketing — Meta
META_PAGE_ID=
META_IG_USER_ID=
META_PAGE_ACCESS_TOKEN=

# Marketing — Slack
SLACK_BOT_TOKEN=
SLACK_TEAM_ID=
```

---

## Key Design Decisions & Gotchas

### Alarm system
Alarms use macOS Clock (`mobiletimerd`) via plist editing. Weekday bitmask is **Monday-first** (bit 0 = Monday). `JARVIS_TIMEZONE` aligns wall-clock times. After any plist edit, `mobiletimerd` is restarted. **This code is frozen — do not touch alarms unless explicitly asked.**

### ReAct duplicate-call guard
Tracks `(tool_name, canonical_args_json)` tuples — not just tool name. Same tool with different args is allowed (e.g. reading two different emails in one turn). Same tool + same args is blocked (infinite loop prevention). First-time param validation errors (Zod schema) get one free retry without counting as a duplicate.

### TTS sentence splitting
`_drain_sentences()` does NOT split at `.` when preceded by a digit — prevents `"1."` becoming a standalone Piper chunk that would be spoken as "one dot". `_clean_for_tts()` has additional guards for mid-sentence ` - ` dashes (→ `, `), `•` bullets, and `\b\d+\.` remnants.

### Marketing routing
`_needs_marketing()` in `main.py` uses keyword detection on the user message. If matched, bypasses the normal ReAct loop entirely and runs `run_marketing_agent()`. The marketing system has its own tool registry (`MARKETING_REGISTRY`) separate from the main `REGISTRY`.

### MCP HTML emails
`_result_to_str()` in `mcp_loader.py` post-processes all MCP results. If the text contains `<html>`, `<table>`, `<div>`, or `<span>`, it runs `_strip_html()` — a minimal HTML-to-text converter using stdlib `html.parser`. This is essential for commercial emails (foodpanda, delivery services) that are HTML-only.

### Proactive alerts
`broadcast_proactive_payload()` fans out to all connected WebSockets. If `speak=True`, the message is also spoken via TTS through the same pipeline as normal responses. All proactive jobs check `_clients_connected()` to avoid speaking into the void.

### Conversation history vs memory
Two separate systems:
- `conversation_log.jsonl` — last 500 turns on disk, last 30 loaded at startup → injected as `history_messages` into LLM context
- ChromaDB — semantic search over all past turns, top results injected as a memory block in the system prompt

### Profile auto-update
After every turn, `schedule_profile_update()` fires an async background LLM call that extracts structured facts from the conversation and merges them into `user_profile.json`. This runs as a `asyncio.create_task()` — never blocks the main response.

---

## How to Run

```bash
# Backend
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000

# Frontend (browser dev)
cd frontend
pnpm dev

# iOS (after any frontend change)
cd frontend
pnpm build:capacitor && pnpm cap:sync
# then ⌘R in Xcode
```

Backend health check: `GET /health` → `{"status":"ok","whisper_loaded":bool,...}`

Debug tool list: `GET /tools` → JSON array of all registered tool definitions (including MCP tools)

---

## Recent Changes (as of 2026-05-20)

1. **ReAct param-error retry** — `agent_loop.py`: first Zod/JSON-schema validation error from a tool allows one retry (tracks `param_retries` per `(action, args)` key). Fixes `gmail__search_emails` being called without `query` on first attempt.

2. **ReAct duplicate-call guard refactor** — Changed from `called_actions: set[str]` (per tool name) to `called_calls: set[tuple[str,str]]` (per tool + canonical args). Allows reading two different emails in one turn. Prompt updated to say "you MAY call the same tool multiple times if arguments differ."

3. **MCP HTML stripping** — `mcp_loader.py`: `_strip_html()` function using stdlib `html.parser`. Applied in `_result_to_str()` when result contains HTML tags. Fixes empty/garbled email bodies from HTML-only commercial emails.

4. **TTS "dot" and "dash" fix** — Two changes:
   - `_drain_sentences()`: skip splitting at `.` preceded by a digit (prevents `"1."` standalone chunks)
   - `_clean_for_tts()`: mid-sentence ` - ` → `, `, `•` stripped, `\b\d+\.` remnants stripped

5. **Proactive system expansion** — `proactive_agent.py`: added `morning_brief_job` (cron 08:00), `smart_email_scan` (every 30m, LLM-judged importance, tracks seen IDs), enriched `idle_reminder` (fetches context before nudging), `evening_wrapup` (cron 18:00, once/day).
