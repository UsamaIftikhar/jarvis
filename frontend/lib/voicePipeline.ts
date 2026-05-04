/**
 * VoicePipeline — orchestrates Phase 3's mic→server→speaker flow.
 *
 * Responsibilities:
 *   1. PCM streamer worklet management (start/stop on demand).
 *   2. Client-side VAD: detect speech start/end so we can fire
 *      `utterance_start` / `utterance_end` over the WebSocket.
 *   3. Quantise Float32 mic frames to Int16 LE and ship them as binary WS
 *      frames in real time while the user speaks.
 *   4. Decode incoming TTS PCM frames and schedule them on a
 *      gap-free `AudioBufferSourceNode` chain so the user hears smooth
 *      audio.
 *   5. Drive the Zustand state machine through THINKING / SPEAKING / IDLE.
 *
 * The pipeline is "armed" once the AudioWorklet modules are loaded and
 * the WS link is up. `arm({...})` / `disarm()` toggle this.
 */

import type { JarvisWsClient, ServerMessage } from "@/lib/wsClient";
import { useJarvis, PRE_SPEECH_TIMEOUT_MS } from "@/lib/state";
import { audioBus } from "@/lib/audioBus";

// VAD parameters. We use an adaptive noise floor on top of an absolute
// minimum so the detector self-tunes to the user's environment.
const VAD = {
  /** Absolute floor — RMS below this is *always* considered silence. */
  speechThreshold: 0.02,
  /** Speech requires RMS > max(speechThreshold, noiseMultiplier * noiseFloor). */
  noiseMultiplier: 1.8,
  /** Frames must exceed threshold for this long before we lock in "speech started". */
  minSpeechMs: 150,
  /** Trailing silence that ends an utterance (longer = fewer mid-sentence cuts). */
  endSilenceMs: 1800,
  /** Hard cap so a single utterance can't exceed this. */
  maxUtteranceMs: 30_000,
  /** EMA half-life for noise-floor tracking, in ms. */
  noiseFloorHalfLifeMs: 3000,
  /** Initial noise-floor estimate (small so the first speech still triggers). */
  initialNoiseFloor: 0.003,
};

/** Set true via `?debug=1` in the URL to dump VAD state to the console. */
let DEBUG = false;
export function setVoicePipelineDebug(on: boolean): void {
  DEBUG = on;
}
function dlog(...args: unknown[]): void {
  if (DEBUG) console.log("[voice]", ...args);
}

interface ArmOptions {
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  ws: JarvisWsClient;
  /** Optional debug callback for the live RMS during recording. */
  onCaptureRms?: (rms: number) => void;
}

export class VoicePipeline {
  private context: AudioContext | null = null;
  private source: MediaStreamAudioSourceNode | null = null;
  private node: AudioWorkletNode | null = null;
  private ws: JarvisWsClient | null = null;
  private armed = false;
  /** In-flight `arm()` promise. `startRecording()` awaits this so an
   * early wake (user clicks Space the instant the mic comes up) doesn't
   * get silently dropped. */
  private armPromise: Promise<void> | null = null;
  /** Bumped on each `startRecording()` entry; stale resumes drop so we never
   * send duplicate `utterance_start` under React Strict or overlapping awaits. */
  private startRecordingNonce = 0;

  // Recording state (only meaningful between utterance_start / _end).
  private recording = false;
  private speechStartedAt: number | null = null;
  private lastLoudAt: number | null = null;
  private utteranceStartedAt: number | null = null;
  private preSpeechTimer: ReturnType<typeof setTimeout> | null = null;
  private onCaptureRms: ((rms: number) => void) | undefined;
  // Adaptive noise-floor (EMA of low RMS values; only updated when we
  // believe we're NOT in active speech).
  private noiseFloor = VAD.initialNoiseFloor;
  private lastFrameTs = 0;

  // Playback state — prefer the mic ``AudioContext`` so TTS shares the same
  // user-gesture unlock (a second context often stays ``suspended`` = silence).
  private playContext: AudioContext | null = null; // only if mic context missing
  private ttsGraphHost: AudioContext | null = null;
  private playGain: GainNode | null = null;
  private playAnalyser: AnalyserNode | null = null;
  private playCursor = 0; // monotonically advancing playback time (sec)
  private ttsSampleRate = 16000;
  private playing = false;
  /** PCM that arrived before ``speaking_start`` (WS ordering / lazy start). */
  private pendingBinaryChunks: ArrayBuffer[] = [];
  private pendingSources: AudioBufferSourceNode[] = [];

  arm(options: ArmOptions): Promise<void> {
    if (this.armed) return Promise.resolve();
    if (this.armPromise) return this.armPromise;
    dlog("arm() begin");
    this.armPromise = this._doArm(options).catch((err) => {
      // IMPORTANT: surface this loudly. Silent rejections in the original
      // implementation are exactly what got us a "pipeline not armed yet"
      // mystery in the first place.
      console.error("[voice] arm() failed:", err);
      this.armPromise = null;
      throw err;
    });
    return this.armPromise;
  }

  private async _doArm(options: ArmOptions): Promise<void> {
    const { context, source, ws } = options;
    this.context = context;
    this.source = source;
    this.ws = ws;
    this.onCaptureRms = options.onCaptureRms;

    // 16 kHz windowSize=320 → 20 ms / 32-bit floats. Browsers usually run
    // the AudioContext at the device rate (often 48 kHz); the worklet is
    // rate-agnostic, we just emit `context.sampleRate` as the declared
    // rate to the server which resamples if needed.
    await context.audioWorklet.addModule("/worklets/pcm-streamer.js");

    this.node = new AudioWorkletNode(context, "pcm-streamer", {
      numberOfInputs: 1,
      numberOfOutputs: 0,
      channelCount: 1,
      processorOptions: { windowSize: 320 },
    });

    this.node.port.onmessage = (
      event: MessageEvent<{ pcm: Float32Array; rms: number; t: number }>,
    ) => this.handleFrame(event.data);

    source.connect(this.node);
    this.armed = true;
    dlog(
      `arm() complete (sampleRate=${context.sampleRate}, ws.isOpen=${ws.isOpen()})`,
    );
    // Wake can land in LISTENING before the mic finishes opening. The page
    // effect then calls startRecording() while nothing is armed yet and no
    // arm() has started — so we must pick up the turn here once wiring exists.
    if (useJarvis.getState().state === "LISTENING") {
      dlog("armed while LISTENING — calling startRecording()");
      void this.startRecording();
    }
  }

  /** True once the pipeline is wired up and ready to record. */
  isArmed(): boolean {
    return this.armed;
  }

  async disarm(): Promise<void> {
    this.stopRecording();
    if (this.preSpeechTimer) {
      clearTimeout(this.preSpeechTimer);
      this.preSpeechTimer = null;
    }
    try {
      this.node?.disconnect();
    } catch {
      /* already disconnected */
    }
    this.node = null;
    this.source = null;
    this.ws = null;
    this.armed = false;
    this.armPromise = null;
    // Flush TTS while ``this.context`` is still set so we disconnect from the mic graph.
    await this.flushPlayback();
    this.context = null;
  }

  /** Begin recording the user's utterance. Idempotent.
   *
   * If `arm()` is still in flight (the user hit Space the instant the
   * mic came online) we await it instead of dropping the wake. */
  async startRecording(): Promise<void> {
    if (!this.ws || !this.context) return;
    // Mic not wired and arm never kicked off — `_doArm` will call `startRecording` again later.
    if (!this.armed && !this.armPromise) {
      dlog(
        "startRecording: mic not wired yet (LISTENING before onMicReady) — will start when arm() completes",
      );
      return;
    }

    const entryNonce = ++this.startRecordingNonce;

    if (!this.armed && this.armPromise) {
      dlog("startRecording: arm() in flight — awaiting…");
      try {
        await this.armPromise;
      } catch {
        // arm() already logged the underlying error.
        return;
      }
    }

    // Superseded by a newer wake / arm-complete call (duplicate effect or Strict remount).
    if (entryNonce !== this.startRecordingNonce) return;
    if (!this.armed || !this.ws || !this.context || this.recording) return;

    // Double-clap wake leaves the clap tail in the mic buffer. Wait for it to
    // dissipate before opening the VAD window, otherwise the transient gets
    // sent to Whisper and produces an empty / garbled transcript.
    const wakeSource = useJarvis.getState().lastWake?.source;
    if (wakeSource === "double-clap") {
      dlog("double-clap wake — 350ms warmup to let clap tail dissipate");
      await new Promise<void>((resolve) => setTimeout(resolve, 350));
      // Check we're still the active recording request and still LISTENING.
      if (entryNonce !== this.startRecordingNonce) return;
      if (useJarvis.getState().state !== "LISTENING") return;
    }

    this.recording = true;
    this.speechStartedAt = null;
    this.lastLoudAt = null;
    this.utteranceStartedAt = performance.now();
    this.lastFrameTs = 0;
    // Re-seed noise floor low so the first real speech burst always
    // qualifies; it'll re-learn within a second of recording.
    this.noiseFloor = VAD.initialNoiseFloor;

    // Tell the worklet to begin emitting frames (it ignores process()
    // output until it sees the start message).
    this.node?.port.postMessage({ type: "start" });

    this.ws.send({
      type: "utterance_start",
      sample_rate: Math.round(this.context.sampleRate),
    });
    dlog("utterance_start sent, sample_rate =", this.context.sampleRate);

    // If the user never speaks, abort the turn cleanly.
    this.preSpeechTimer = setTimeout(() => {
      if (this.recording && this.speechStartedAt === null) {
        this.cancelTurn("no speech detected");
      }
    }, PRE_SPEECH_TIMEOUT_MS);
  }

  /** End recording without sending utterance_end (used for cancellation). */
  private stopRecording(): void {
    if (!this.recording) return;
    this.recording = false;
    this.node?.port.postMessage({ type: "stop" });
    if (this.preSpeechTimer) {
      clearTimeout(this.preSpeechTimer);
      this.preSpeechTimer = null;
    }
  }

  private handleFrame({
    pcm,
    rms,
  }: {
    pcm: Float32Array;
    rms: number;
    t: number;
  }): void {
    if (!this.recording || !this.ws) return;
    this.onCaptureRms?.(rms);

    const now = performance.now();
    // Effective speech threshold: max of an absolute floor and a multiple
    // of the running noise floor. Adapts to noisy rooms automatically.
    const effThreshold = Math.max(
      VAD.speechThreshold,
      this.noiseFloor * VAD.noiseMultiplier,
    );
    const isLoud = rms > effThreshold;

    if (isLoud) {
      this.lastLoudAt = now;
      if (this.speechStartedAt === null) {
        this.speechStartedAt = now;
        dlog(
          `speech started: rms=${rms.toFixed(3)} thr=${effThreshold.toFixed(
            3,
          )} noise=${this.noiseFloor.toFixed(3)}`,
        );
      }
    } else {
      // Update the noise-floor EMA only when we're confident this isn't
      // speech. dt-aware so it works regardless of frame cadence.
      const dt = this.lastFrameTs === 0 ? 0 : now - this.lastFrameTs;
      this.lastFrameTs = now;
      if (dt > 0) {
        const k = 1 - Math.pow(0.5, dt / VAD.noiseFloorHalfLifeMs);
        this.noiseFloor = this.noiseFloor + (rms - this.noiseFloor) * k;
      }
    }

    // Stream the frame to the server as Int16 LE.
    const int16 = float32ToInt16(pcm);
    this.ws.sendBinary(int16.buffer as ArrayBuffer);

    // VAD end-of-utterance check.
    if (
      this.speechStartedAt !== null &&
      now - this.speechStartedAt >= VAD.minSpeechMs &&
      this.lastLoudAt !== null &&
      now - this.lastLoudAt >= VAD.endSilenceMs
    ) {
      dlog("utterance ended (silence > endSilenceMs)");
      this.commitUtterance();
      return;
    }

    // Hard cap.
    if (
      this.utteranceStartedAt !== null &&
      now - this.utteranceStartedAt >= VAD.maxUtteranceMs
    ) {
      dlog("utterance ended (hard cap reached)");
      this.commitUtterance();
    }
  }

  private commitUtterance(): void {
    if (!this.ws) return;
    this.stopRecording();
    this.ws.send({ type: "utterance_end" });
    useJarvis.getState().toThinking();
  }

  private cancelTurn(reason: string): void {
    if (!this.ws) return;
    this.pendingBinaryChunks = [];
    this.stopRecording();
    this.ws.send({ type: "cancel" });
    useJarvis.getState().sleep();
    // Surface a quiet status message so the user knows what happened.
    useJarvis.getState().setLastMessage(`Turn aborted: ${reason}.`);
  }

  // -------------------------------------------------------------------
  // Server message handling — call this from the WS onMessage callback.
  // -------------------------------------------------------------------

  handleServerMessage(msg: ServerMessage): void {
    const store = useJarvis.getState();
    switch (msg.type) {
      case "thinking":
        store.toThinking();
        break;
      case "transcript":
        store.setTranscript(msg.text);
        break;
      case "token":
        store.appendResponse(msg.text);
        break;
      case "final":
        store.setResponse(msg.text);
        break;
      case "speaking_start":
        this.beginPlayback(msg.sample_rate);
        store.toSpeaking();
        break;
      case "speaking_end":
        // Schedule a state transition for after the queued audio plays out.
        this.endPlaybackWhenIdle();
        break;
      case "error":
        store.setError(msg.message);
        this.pendingBinaryChunks = [];
        // Give up on the current turn.
        this.stopRecording();
        break;
      default:
        // pong / hello / echo handled outside the pipeline.
        break;
    }
  }

  /** Forward a binary frame from the WS layer (TTS PCM). */
  handleServerBinary(data: ArrayBuffer): void {
    if (!this.playing) {
      // Rare: binary can arrive before the JSON ``speaking_start`` is handled.
      if (this.pendingBinaryChunks.length < 64) this.pendingBinaryChunks.push(data);
      return;
    }
    this.scheduleChunk(data);
  }

  // -------------------------------------------------------------------
  // Playback queue
  // -------------------------------------------------------------------

  /** Mic context if armed, else the dedicated TTS-only context (may be suspended). */
  private playbackAudioContext(): AudioContext | null {
    return this.context ?? this.playContext;
  }

  private beginPlayback(sampleRate: number): void {
    const sr = Number(sampleRate);
    this.ttsSampleRate =
      Number.isFinite(sr) && sr >= 8000 && sr <= 48000 ? sr : 16000;

    if (this.context) {
      if (this.playContext && this.playContext !== this.context) {
        try {
          void this.playContext.close();
        } catch {
          /* already closed */
        }
        this.playContext = null;
      }
    } else if (!this.playContext) {
      this.playContext = new AudioContext({
        sampleRate: this.ttsSampleRate,
      });
    }

    const ctx = this.playbackAudioContext();
    if (!ctx) {
      dlog("beginPlayback: no AudioContext — cannot play TTS");
      return;
    }

    // Rebuild the output chain every time: some browsers suspend or drop
    // connections after a turn; incremental wiring can leave us silent.
    try {
      this.playGain?.disconnect();
    } catch {
      /* already disconnected */
    }
    try {
      this.playAnalyser?.disconnect();
    } catch {
      /* already disconnected */
    }
    audioBus.setPlaybackAnalyser(null);

    this.playGain = ctx.createGain();
    this.playGain.gain.value = 1.0;
    this.playAnalyser = ctx.createAnalyser();
    this.playAnalyser.fftSize = 1024;
    this.playAnalyser.smoothingTimeConstant = 0.55;
    this.playGain.connect(this.playAnalyser);
    this.playAnalyser.connect(ctx.destination);
    audioBus.setPlaybackAnalyser(this.playAnalyser);
    this.ttsGraphHost = ctx;

    this.playCursor = ctx.currentTime + 0.05;
    this.playing = true;

    const flushAfterRunning = (): void => {
      const pre = this.pendingBinaryChunks.length;
      const now = ctx.currentTime;
      if (this.playCursor < now) this.playCursor = now + 0.02;
      this.flushPendingBinary();
      dlog(
        `speaking_start: ctx.state=${ctx.state} rate=${this.ttsSampleRate}Hz pcm_queued_before_flush=${pre}`,
      );
    };

    if (ctx.state === "suspended") {
      void ctx.resume().then(flushAfterRunning, flushAfterRunning);
    } else {
      flushAfterRunning();
    }
  }

  private flushPendingBinary(): void {
    if (!this.playing) return;
    for (const buf of this.pendingBinaryChunks) this.scheduleChunk(buf);
    this.pendingBinaryChunks = [];
  }

  private scheduleChunk(data: ArrayBuffer): void {
    const ctx = this.playbackAudioContext();
    if (!ctx || !this.playGain) return;
    if (ctx.state === "suspended") {
      void ctx.resume();
    }
    const int16 = new Int16Array(data);
    if (int16.length === 0) return;
    const float32 = new Float32Array(int16.length);
    for (let i = 0; i < int16.length; i++) {
      float32[i] = (int16[i] ?? 0) / 32768;
    }
    const buffer = ctx.createBuffer(1, float32.length, this.ttsSampleRate);
    buffer.getChannelData(0).set(float32);

    const src = ctx.createBufferSource();
    src.buffer = buffer;
    src.connect(this.playGain);

    const now = ctx.currentTime;
    if (this.playCursor < now) this.playCursor = now;
    src.start(this.playCursor);
    this.playCursor += buffer.duration;

    this.pendingSources.push(src);
    src.onended = () => {
      this.pendingSources = this.pendingSources.filter((s) => s !== src);
    };
  }

  private endPlaybackWhenIdle(): void {
    const ctx = this.playbackAudioContext();
    if (!ctx) {
      useJarvis.getState().sleep();
      return;
    }
    const fireWhenDone = () => {
      const remaining = Math.max(0, this.playCursor - ctx.currentTime);
      // Add a small tail so we don't cut off the last word.
      window.setTimeout(
        () => {
          this.playing = false;
          // Only return to IDLE if we're still in SPEAKING — a new turn
          // may have started.
          if (useJarvis.getState().state === "SPEAKING") {
            useJarvis.getState().sleep();
          }
        },
        Math.ceil(remaining * 1000) + 60,
      );
    };
    fireWhenDone();
  }

  private async flushPlayback(): Promise<void> {
    this.pendingSources.forEach((s) => {
      try {
        s.stop();
      } catch {
        /* already stopped */
      }
    });
    this.pendingSources = [];
    this.pendingBinaryChunks = [];
    this.playing = false;
    audioBus.setPlaybackAnalyser(null);
    try {
      this.playGain?.disconnect();
    } catch {
      /* already disconnected */
    }
    try {
      this.playAnalyser?.disconnect();
    } catch {
      /* already disconnected */
    }
    this.playGain = null;
    this.playAnalyser = null;
    this.ttsGraphHost = null;
    // Never close the mic ``AudioContext`` — only our dedicated fallback.
    if (this.playContext && this.playContext !== this.context) {
      if (this.playContext.state !== "closed") {
        try {
          await this.playContext.close();
        } catch {
          /* already closed */
        }
      }
      this.playContext = null;
    }
  }
}

// --- helpers --------------------------------------------------------

function float32ToInt16(input: Float32Array): Int16Array {
  const out = new Int16Array(input.length);
  for (let i = 0; i < input.length; i++) {
    let s = input[i] ?? 0;
    if (s > 1) s = 1;
    else if (s < -1) s = -1;
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}
