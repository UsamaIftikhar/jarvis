"use client";

import { useJarvis } from "@/lib/state";

/**
 * Centred HUD-style prompt that asks the user to grant mic access.
 *
 * `getUserMedia` requires a user gesture, so we render this overlay until
 * the user clicks. After click we flip `audio` to "requesting" and let
 * `<AudioCapture />` perform the actual permission flow + worklet boot.
 */
export function MicPrompt() {
  const audio = useJarvis((s) => s.audio);
  const audioError = useJarvis((s) => s.audioError);
  const setAudio = useJarvis((s) => s.setAudio);

  // Only the "needs gesture" and "denied/error" states render UI.
  if (audio === "ready" || audio === "requesting") return null;

  const isFailure = audio === "denied" || audio === "error";
  const headline = isFailure
    ? "Audio Subsystem Offline"
    : "Initialize Audio Subsystem";
  const subline = isFailure
    ? (audioError ?? "Microphone unavailable.")
    : "Tap to grant microphone access for wake-word detection.";
  const cta = isFailure ? "Retry" : "Engage";

  return (
    <div className="pointer-events-none fixed inset-0 z-20 flex items-center justify-center">
      <button
        type="button"
        onClick={() => setAudio("requesting")}
        className="hud-bracket pointer-events-auto group flex flex-col items-center gap-3 border border-jarvis-blue/40 bg-jarvis-bg/70 px-10 py-6 backdrop-blur-sm transition hover:border-jarvis-blue hover:shadow-glow-blue"
      >
        <div className="font-display text-[10px] uppercase tracking-[0.5em] text-jarvis-muted">
          j.a.r.v.i.s.
        </div>
        <div
          className={`font-display text-xl font-bold uppercase tracking-[0.4em] ${
            isFailure ? "text-jarvis-danger" : "text-jarvis-blue"
          }`}
          style={{
            textShadow: isFailure
              ? "0 0 12px rgba(255, 58, 85, 0.6)"
              : "0 0 12px rgba(0, 212, 255, 0.6)",
          }}
        >
          {headline}
        </div>
        <div className="max-w-sm text-center font-body text-sm text-jarvis-text/70">
          {subline}
        </div>
        <div className="mt-2 font-display text-xs uppercase tracking-[0.5em] text-jarvis-blue group-hover:text-jarvis-cyan">
          [ {cta} ]
        </div>
      </button>
    </div>
  );
}
