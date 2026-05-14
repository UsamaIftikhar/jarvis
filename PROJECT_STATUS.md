# JARVIS — Project Status & Architecture

A **voice-oriented AI assistant** (Iron Man–style J.A.R.V.I.S.) with a futuristic Next.js HUD, Python FastAPI backend, local Whisper STT, DeepSeek LLM, Piper/ElevenLabs TTS, ChromaDB memory, 20+ built-in tools, MCP server support, and a native iOS app with always-on "Hey JARVIS" wake word.

---

## How to Run

**Backend:**
```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

**Frontend (browser dev):**
```bash
cd frontend
pnpm dev
```

**iOS app (after any frontend change):**
```bash
cd frontend
pnpm build:capacitor && pnpm cap:sync
# then ⌘R in Xcode
```

---

## Backend Architecture

| File | Role |
|------|------|
| `main.py` | FastAPI app, WebSocket protocol, lifespan (MCP startup/shutdown), `/tools` debug endpoint |
| `agent_loop.py` | ReAct loop — DeepSeek returns `{thought, action, args}` JSON, tools executed, scratchpad built, streamed final answer |
| `llm.py` | DeepSeek streaming client, `JARVIS_SYSTEM_PROMPT` (brevity, tool rules, personality, "sir") |
| `tts.py` | Piper CLI (`--output-raw` streaming, `length_scale`) + ElevenLabs; `chunk_text_for_tts` for sentence-level streaming |
| `stt.py` | Whisper local transcription |
| `user_profile.py` | User profile storage + `/profile/status` endpoint |
| `situational.py` | Time/weather context injected into every prompt |
| `proactive_agent.py` | Scheduled proactive jobs, reminder broadcasts |
| `memory/` | ChromaDB long-term embeddings (`JARVIS_CHROMA_PATH`) |

### Tools package (`tools/`)

Refactored from a single `tools.py` monolith into a proper registry package:

- **`tools/registry.py`** — `ToolEntry` dataclass + `Registry` class. All lookups (`thinking_labels`, `terminal_tools`, `known_names`) are live at call-time so MCP tools registered after startup are always visible.
- **`tools/__init__.py`** — imports all sub-modules, exposes `TOOL_DEFINITIONS`, `run_tool()`, `configure_tools()`
- **`tools/mcp_loader.py`** — MCP client. Reads `backend/mcp_servers.json`, spawns each server as a child process via stdio, discovers tools via `session.list_tools()`, registers them as `servername__toolname` in the registry.
- Sub-modules: `alarms`, `calendar_tool`, `filesystem`, `notes`, `reminders`, `stopwatch`, `system`, `vision`, `web`

### MCP Integration

External tool servers are configured in `backend/mcp_servers.json`:
```json
[{ "name": "gmail", "command": "npx", "args": ["-y", "@gongrzhe/server-gmail-autoauth-mcp"] }]
```
MCP servers start at app lifespan and shut down cleanly. Tools appear automatically in the agent's tool list. Currently connected: **Gmail** (read, search, send, labels).

OAuth credentials: `~/.gmail-mcp/gcp-oauth.keys.json`. Token cached after first browser auth.

---

## Frontend Architecture

| File | Role |
|------|------|
| `app/page.tsx` | Root page — WebSocket lifecycle, mic pipeline, settings, keyboard shortcuts |
| `lib/voicePipeline.ts` | STT → WS → TTS playback. PCM worklet → Int16 frames → server; incoming PCM → `AudioBufferSourceNode` chain |
| `lib/wsClient.ts` | Typed WebSocket client with reconnect |
| `lib/state.ts` | Zustand store — states: `IDLE / LISTENING / THINKING / SPEAKING / ERROR` |
| `lib/jarvisEndpoints.ts` | Backend URL resolution: localStorage → env var → localhost default |
| `lib/nativeBridge.ts` | Capacitor bridge to `JarvisWakePlugin` (native wake word) |
| `components/WakeDetector.tsx` | Wake word: native path (iOS) or Web Speech API fallback (desktop) |
| `components/BackendSettings.tsx` | In-app settings modal to enter Tailscale IP; tests `/health` before saving |
| `components/JarvisCanvas.tsx` | Three.js HUD orb |
| `components/StatusOverlay.tsx` | State badge, transcript, streaming reply, proactive alerts |

---

## iOS Mobile App (Capacitor)

Next.js exports as a static site wrapped in a native iOS app via Capacitor.

**Build targets:**
- `pnpm build:capacitor` — static export (`CAPACITOR_BUILD=1`)
- `pnpm cap:sync` — copies `out/` into the Xcode project
- `pnpm cap:open` — opens Xcode

**Native plugin — `JarvisWakePlugin.swift`:**
- `AVAudioSession.playAndRecord` with `.defaultToSpeaker` (set in `AppDelegate.swift` at launch)
- Silent PCM buffer loop via `AVAudioPlayerNode` — keeps app alive in iOS background (background audio entitlement)
- `SFSpeechRecognizer` with `requiresOnDeviceRecognition = true` — on-device, no network needed
- Fires `"wakeWord"` Capacitor event to the web layer; restarts recognition automatically (~1 min session limit)
- Handles audio interruptions (calls, Siri)

**Backend connectivity:**
- Tailscale VPN mesh — Mac and iPhone share a private `100.x.x.x` IP
- Backend URL stored in `localStorage["jarvis_backend_url"]` — set at runtime via the ⚙ settings gear
- No rebuild required to change backend IP

**Info.plist permissions:** `NSMicrophoneUsageDescription`, `NSSpeechRecognitionUsageDescription`, `UIBackgroundModes: [audio]`, `NSAppTransportSecurity: NSAllowsArbitraryLoads` (for HTTP over Tailscale)

**Installation:** Free Apple ID personal team in Xcode — no paid developer account needed. App expires every 7 days, re-run from Xcode to refresh.

---

## TTS

| Setting | Value |
|---------|-------|
| Provider | `JARVIS_TTS_PROVIDER=piper` (or `elevenlabs`) |
| Model | `en_US-lessac-medium.onnx` (natural, clear) — was `danny-low` (slow/robotic) |
| Streaming | `--output-raw` mode — first audio chunk plays ~100ms after synthesis starts |
| Speed | `PIPER_LENGTH_SCALE=0.9` default (< 1.0 = faster) |
| Fallback | ElevenLabs keys in `.env` — switch with `JARVIS_TTS_PROVIDER=elevenlabs` |

---

## Built-in Tools (20+)

| Area | Tools |
|------|-------|
| Web | Tavily search |
| Filesystem | List/read files under `JARVIS_FS_ROOT` sandbox |
| Calendar | Read events from `.ics` file (`JARVIS_CALENDAR_ICS`) |
| Alarms | Create, cancel, delete macOS Clock alarms (weekday-aware bitmask) |
| Reminders | In-session HUD + TTS + Apple Reminders via AppleScript |
| System | Volume, brightness, clipboard, open apps |
| Browser | Open URL, search |
| Screenshot | Vision via Anthropic API |
| Stopwatch | In-memory timer |
| Notes | macOS Notes via AppleScript |
| **Gmail (MCP)** | Read, search, send, label emails — OAuth authenticated |

---

## Environment Keys (`.env`)

```
DEEPSEEK_API_KEY        LLM
DEEPSEEK_MODEL          deepseek-chat
PIPER_CMD               path to piper binary
PIPER_MODEL             path to .onnx voice model
PIPER_LENGTH_SCALE      speech speed (default 0.9)
PIPER_PREFIX_ARGS       arch -x86_64 on Apple Silicon with Rosetta Piper
PIPER_DYLD_LIBRARY_PATH dylib path for espeak-ng
ELEVENLABS_API_KEY      optional cloud TTS
ELEVENLABS_VOICE_ID
WHISPER_MODEL           tiny / base / small.en / medium / large-v3
TAVILY_API_KEY          web search
ANTHROPIC_API_KEY       screenshot vision (optional)
JARVIS_FS_ROOT          sandboxed filesystem root
JARVIS_CALENDAR_ICS     path to .ics calendar export
JARVIS_CHROMA_PATH      ChromaDB store path
JARVIS_TIMEZONE         optional TZ override for alarm wall-clock alignment
GEMINI_API_KEY          (available, not yet wired to tools)
```

---

## Alarm Code Policy

Alarm-related paths are treated as **frozen** unless explicitly requested. See `.cursor/rules/do-not-touch-alarms.mdc`.

---

## Notable Implementation History

1. **Tools refactor** — `tools.py` monolith → `tools/` registry package. `ToolEntry` dataclass, live lookups so MCP tools are always visible to the agent.
2. **MCP client** — `mcp_loader.py` with `AsyncExitStack`, dynamic tool registration, `mcp_servers.json` config.
3. **Gmail OAuth** — `@gongrzhe/server-gmail-autoauth-mcp`, credentials at `~/.gmail-mcp/gcp-oauth.keys.json`, test user added to Google Cloud OAuth consent screen.
4. **Agent prompt fix** — `_thinking_labels` and `_terminal_tools` were module-level constants (set before MCP loaded). Made live lookups. Prompt rewritten to say tools are "LIVE and authenticated" — previously DeepSeek refused to use Gmail.
5. **iOS app** — Capacitor wrapper, `JarvisWakePlugin.swift` (silent audio + on-device wake word), `AppDelegate` sets loudspeaker routing at launch.
6. **ATS fix** — `NSAllowsArbitraryLoads` in `Info.plist` needed for HTTP connections to Tailscale IP from WKWebView.
7. **Hydration fix** — `isNativePlatform()` called at render time caused React error #418. Moved into `useEffect` + `useState`.
8. **TTS streaming** — Switched from temp WAV file (blocking) to `--output-raw` stdout streaming; added `length_scale=0.9`; switched model from `danny-low` to `lessac-medium`.
9. **Clock alarms** — Monday-first weekday bitmask, `JARVIS_TIMEZONE` support, `mobiletimerd` restart after plist edits.
10. **Reminders** — Native Apple Reminders creation via AppleScript alongside in-session HUD.
