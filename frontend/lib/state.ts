import { create } from "zustand";

/**
 * The core JARVIS state machine. Each phase will read/transition this:
 *  - IDLE       : counter-rotating rings, awaiting wake
 *  - LISTENING  : mic open, capturing audio
 *  - THINKING   : STT done, LLM is generating
 *  - SPEAKING   : TTS audio is playing back
 *  - ERROR      : transient error display
 */
export type JarvisState =
  | "IDLE"
  | "LISTENING"
  | "THINKING"
  | "SPEAKING"
  | "ERROR";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";

export type AudioStatus =
  | "idle"
  | "requesting"
  | "ready"
  | "denied"
  | "error";

export type WakeSource = "double-clap" | "hey-jarvis" | "manual";

/**
 * Maximum time to wait for the user to start talking after wake. If we
 * see no speech in this window we drop back to IDLE so the HUD doesn't
 * get stuck. Phase 3+ uses VAD to end utterances naturally; this is just
 * the "user changed their mind" timeout.
 */
export const PRE_SPEECH_TIMEOUT_MS = 5000;

interface JarvisStore {
  state: JarvisState;
  connection: ConnectionStatus;
  audio: AudioStatus;
  audioError: string | null;

  lastWake: { ts: number; source: WakeSource } | null;

  /** Most recent finalised user transcript (post-STT). */
  transcript: string | null;
  /** Currently-streaming LLM response. Cleared at the start of each turn. */
  response: string;

  /** Sub-label during THINKING (ReAct tool step), not spoken. */
  thinkingStep: string | null;
  /** Background proactive HUD line (fades for low priority). */
  proactiveAlert: {
    message: string;
    priority: "low" | "medium" | "high";
    /** Optional heading (e.g. named in-session reminder). */
    title?: string;
  } | null;
  /** First-launch wizard; cleared after successful setup POST. */
  needsProfileSetup: boolean;

  lastMessage: string | null;
  errorMessage: string | null;

  // Setters
  setConnection: (next: ConnectionStatus) => void;
  setAudio: (next: AudioStatus, errorMessage?: string | null) => void;
  setLastMessage: (msg: string | null) => void;
  setError: (msg: string | null) => void;
  setTranscript: (text: string | null) => void;
  resetResponse: () => void;
  appendResponse: (delta: string) => void;
  setResponse: (text: string) => void;
  setThinkingStep: (label: string | null) => void;
  setProactiveAlert: (
    alert: {
      message: string;
      priority: "low" | "medium" | "high";
      title?: string;
    } | null,
  ) => void;
  setNeedsProfileSetup: (v: boolean) => void;

  // State-machine transitions.
  wake: (source: WakeSource) => void;
  /** Skip LISTENING — used by text/silent-command so the voice pipeline never starts. */
  wakeText: () => void;
  toThinking: () => void;
  toSpeaking: () => void;
  sleep: () => void;
}

export const useJarvis = create<JarvisStore>((set, get) => ({
  state: "IDLE",
  connection: "disconnected",
  audio: "idle",
  audioError: null,
  lastWake: null,
  transcript: null,
  response: "",
  thinkingStep: null,
  proactiveAlert: null,
  needsProfileSetup: false,
  lastMessage: null,
  errorMessage: null,

  setConnection: (next) => set({ connection: next }),
  setAudio: (next, errorMessage = null) =>
    set({ audio: next, audioError: errorMessage }),
  setLastMessage: (msg) => set({ lastMessage: msg }),
  setError: (msg) =>
    set({ errorMessage: msg, state: msg ? "ERROR" : "IDLE" }),
  setTranscript: (text) => set({ transcript: text }),
  resetResponse: () => set({ response: "" }),
  appendResponse: (delta) => set({ response: get().response + delta }),
  setResponse: (text) => set({ response: text }),
  setThinkingStep: (label) => set({ thinkingStep: label }),
  setProactiveAlert: (alert) => set({ proactiveAlert: alert }),
  setNeedsProfileSetup: (v) => set({ needsProfileSetup: v }),

  wake: (source) => {
    if (get().state !== "IDLE" && get().state !== "ERROR") return;
    set({
      state: "LISTENING",
      lastWake: { ts: Date.now(), source },
      transcript: null,
      response: "",
      thinkingStep: null,
      errorMessage: null,
    });
  },
  wakeText: () => {
    if (get().state !== "IDLE" && get().state !== "ERROR") return;
    set({ state: "THINKING", transcript: null, response: "", thinkingStep: null, errorMessage: null });
  },
  toThinking: () => {
    if (get().state !== "LISTENING") return;
    set({ state: "THINKING" });
  },
  toSpeaking: () => {
    // Allowed from THINKING (normal) or LISTENING (very fast path).
    const s = get().state;
    if (s !== "THINKING" && s !== "LISTENING") return;
    set({ state: "SPEAKING" });
  },
  sleep: () => set({ state: "IDLE" }),
}));
