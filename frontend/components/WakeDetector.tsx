"use client";

import { useEffect } from "react";
import { useJarvis } from "@/lib/state";

// Trigger phrases + common mishears of "Hey JARVIS"
const WAKE_PHRASES = [
  "hey jarvis",
  "jarvis are you with me",
  "wake up jarvis",
  "jarvis",        // bare name — catches "just jarvis" and any phrase containing it
  "hey javis",     // mishear: dropped r
  "hey travis",    // mishear
  "hey davis",     // mishear
];

function matchesWake(transcript: string): boolean {
  const t = transcript.toLowerCase();
  return WAKE_PHRASES.some((p) => t.includes(p));
}

/**
 * Always-on wake word detector using the browser's Web Speech API.
 * Runs only while the mic is open and JARVIS is IDLE/ERROR.
 * Pauses automatically during LISTENING / THINKING / SPEAKING so JARVIS
 * doesn't hear its own voice or interrupt an active turn.
 */
export function WakeDetector() {
  const state = useJarvis((s) => s.state);
  const audio = useJarvis((s) => s.audio);
  const wake = useJarvis((s) => s.wake);

  const shouldRun = audio === "ready" && (state === "IDLE" || state === "ERROR");

  useEffect(() => {
    if (!shouldRun) return;

    const Ctor = window.SpeechRecognition ?? window.webkitSpeechRecognition;

    if (!Ctor) {
      console.warn("[WakeDetector] Web Speech API not available in this browser");
      return;
    }

    let cancelled = false;
    let current: SpeechRecognition | null = null;

    function start() {
      if (cancelled) return;

      const rec = new Ctor!();
      current = rec;
      rec.continuous = true;
      rec.interimResults = true;
      rec.lang = "en-US";
      rec.maxAlternatives = 1;

      rec.onresult = (event: SpeechRecognitionEvent) => {
        if (cancelled) return;
        for (let i = event.resultIndex; i < event.results.length; i++) {
          const transcript = event.results[i]?.[0]?.transcript ?? "";
          if (matchesWake(transcript)) {
            wake("hey-jarvis");
            return;
          }
        }
      };

      // Browser ends the session after ~5s silence — restart automatically
      rec.onend = () => {
        if (!cancelled) setTimeout(start, 250);
      };

      rec.onerror = (event: SpeechRecognitionErrorEvent) => {
        if (event.error === "not-allowed" || event.error === "service-not-allowed") {
          cancelled = true;
          console.warn("[WakeDetector] Mic permission denied — wake word disabled");
        }
        // Other errors: onend fires next and handles restart
      };

      try {
        rec.start();
      } catch {
        if (!cancelled) setTimeout(start, 1000);
      }
    }

    start();

    return () => {
      cancelled = true;
      try {
        current?.stop();
      } catch {
        /* already stopped */
      }
    };
  }, [shouldRun, wake]);

  return null;
}
