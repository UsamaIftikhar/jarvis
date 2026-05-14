"use client";

import { useEffect } from "react";
import { useJarvis } from "@/lib/state";
import {
  isNativePlatform,
  startNativeWakeWord,
  stopNativeWakeWord,
} from "@/lib/nativeBridge";

const WAKE_PHRASES = [
  "hey jarvis",
  "jarvis are you with me",
  "wake up jarvis",
  "jarvis",
  "hey javis",
  "hey travis",
  "hey davis",
];

function matchesWake(transcript: string): boolean {
  const t = transcript.toLowerCase();
  return WAKE_PHRASES.some((p) => t.includes(p));
}

export function WakeDetector() {
  const state = useJarvis((s) => s.state);
  const audio = useJarvis((s) => s.audio);
  const wake = useJarvis((s) => s.wake);

  const shouldRun = audio === "ready" && (state === "IDLE" || state === "ERROR");

  // ── Native path (iOS Capacitor) ──────────────────────────────────────────
  useEffect(() => {
    if (!isNativePlatform()) return;
    if (!shouldRun) {
      stopNativeWakeWord();
      return;
    }
    let active = true;
    startNativeWakeWord(() => {
      if (active) wake("hey-jarvis");
    });
    return () => {
      active = false;
      stopNativeWakeWord();
    };
  }, [shouldRun, wake]);

  // ── Web Speech API path (desktop / browser) ──────────────────────────────
  useEffect(() => {
    if (isNativePlatform()) return;
    if (!shouldRun) return;

    const Ctor =
      (window as typeof window & { SpeechRecognition?: typeof SpeechRecognition })
        .SpeechRecognition ??
      (window as typeof window & { webkitSpeechRecognition?: typeof SpeechRecognition })
        .webkitSpeechRecognition;

    if (!Ctor) {
      console.warn("[WakeDetector] Web Speech API not available");
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
      rec.onend = () => { if (!cancelled) setTimeout(start, 250); };
      rec.onerror = (event: SpeechRecognitionErrorEvent) => {
        if (
          event.error === "not-allowed" ||
          event.error === "service-not-allowed"
        ) {
          cancelled = true;
          console.warn("[WakeDetector] Mic permission denied");
        }
      };
      try { rec.start(); } catch { if (!cancelled) setTimeout(start, 1000); }
    }

    start();
    return () => {
      cancelled = true;
      try { current?.stop(); } catch { /* already stopped */ }
    };
  }, [shouldRun, wake]);

  return null;
}
