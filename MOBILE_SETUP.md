# JARVIS iOS App — Setup & Installation Guide

This guide walks you through getting the JARVIS iOS app running on your iPhone with always-on "Hey JARVIS" wake word and a secure Tailscale connection to your Mac backend.

---

## Prerequisites

- **Mac** with Xcode 15+ installed (free from the App Store)
- **iPhone** running iOS 14 or later
- **Apple Developer account** — a free personal account is fine for sideloading to your own phone
- **Node.js / pnpm** already installed on your Mac (you already have this)

---

## Part 1 — Tailscale (Secure Tunnel from iPhone to Mac)

Tailscale creates a private VPN mesh between your devices. Your iPhone connects to your Mac by a stable private IP even when you're not on the same Wi-Fi.

### 1.1 Install on your Mac

```bash
brew install tailscale
sudo tailscaled &          # start the daemon
sudo tailscale up          # log in with your Tailscale account (creates one free)
tailscale ip -4            # note this IP — you'll enter it in the app
```

Or download the Mac app from https://tailscale.com/download

### 1.2 Install on your iPhone

1. Open the App Store and search **Tailscale**
2. Install and log in with the **same** Tailscale account as your Mac
3. After both devices are connected, they get stable IPs like `100.x.x.x`
4. On your Mac run `tailscale ip -4` to find your Mac's Tailscale IP

### 1.3 Start the JARVIS backend

```bash
cd /Users/usama/Desktop/workspace/jarvis/backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

The `--host 0.0.0.0` makes it reachable from other devices on your Tailscale network.

---

## Part 2 — Build and Install the iOS App

### 2.1 Build the static web export

```bash
cd /Users/usama/Desktop/workspace/jarvis/frontend
pnpm build:capacitor     # builds Next.js → out/
pnpm cap:sync            # copies out/ into the Xcode project
```

### 2.2 Open Xcode

```bash
pnpm cap:open            # opens the Xcode project automatically
```

Or open manually: **File → Open** → `frontend/ios/App/App.xcworkspace`

> **Important:** Open `App.xcworkspace`, NOT `App.xcodeproj`

### 2.3 Sign the app

1. In Xcode, click **App** in the project navigator (left sidebar)
2. Select the **App** target → **Signing & Capabilities** tab
3. Under **Team**, select your Apple ID (sign in at Xcode → Settings → Accounts if needed)
4. Xcode will auto-generate a provisioning profile
5. Change the **Bundle Identifier** to something unique if there's a conflict, e.g. `com.yourname.jarvis`

### 2.4 Connect your iPhone and run

1. Plug your iPhone into your Mac with a USB cable
2. In Xcode, select your iPhone in the device picker (top toolbar)
3. Click the **▶ Run** button (or ⌘R)
4. First time: your iPhone will say "Untrusted Developer" — go to **Settings → General → VPN & Device Management → [your Apple ID] → Trust**
5. Run again — the app will open on your phone

---

## Part 3 — First Launch

### 3.1 Grant permissions

When the app first opens, iOS will ask for:
- **Microphone** — tap **Allow**
- **Speech Recognition** — tap **Allow**

These are required for wake word detection.

### 3.2 Connect to your Mac backend

1. Tap the **⚙** gear icon (bottom-right of the JARVIS screen)
2. Enter your Mac's Tailscale IP: `http://100.x.x.x:8000`
3. Tap **CONNECT** — it will test the connection and show ✓ if it works
4. The app reconnects to the new backend immediately

### 3.3 Test the wake word

Say **"Hey JARVIS"** — the orb should activate and start listening.

Other wake phrases that work:
- "Jarvis"
- "Wake up Jarvis"
- "Hey Javis" (common mispronunciation)
- "Hey Travis" (similar sound)

---

## Part 4 — Background Wake Word

The app uses a **silent audio loop trick** to stay alive in the background:
- A zeroed PCM buffer plays on repeat — iOS treats this as an active audio app
- This keeps `SFSpeechRecognizer` running even when the screen is off
- Recognition is **fully on-device** (no internet needed for wake word)

To enable: simply put the app in the background (press Home) — wake word detection continues automatically.

---

## Rebuilding After Code Changes

Any time you change the frontend code:

```bash
cd frontend
pnpm build:capacitor   # rebuild
pnpm cap:sync          # sync to Xcode
# Then hit ▶ Run in Xcode again
```

Any time you change only the backend:

```bash
cd backend
uv run uvicorn main:app --host 0.0.0.0 --port 8000
```
No Xcode rebuild needed — the app fetches from your Mac at runtime.

---

## Troubleshooting

| Problem | Fix |
|---|---|
| "Untrusted Developer" on iPhone | Settings → General → VPN & Device Management → Trust your Apple ID |
| Can't connect to backend | Make sure both devices show green in Tailscale app; verify `--host 0.0.0.0` flag on uvicorn |
| Wake word not firing | Check microphone permission in Settings → Privacy → Microphone → JARVIS |
| App crashes on launch | In Xcode, check the console output; usually a missing permission in Info.plist |
| Xcode "No account" error | Xcode → Settings → Accounts → add your Apple ID |
| Backend URL resets | The URL is saved in the app's localStorage and persists across launches |
