"use client";

import { useEffect, useRef } from "react";
import { useJarvis } from "@/lib/state";
import { openMic, type MicSource } from "@/lib/audioUtils";
import {
  DEFAULT_CLAP_PARAMS,
  DoubleClapDetector,
} from "@/lib/clapDetector";
import { audioBus } from "@/lib/audioBus";

type RmsListener = (rms: number) => void;

export interface MicReady {
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
}

interface AudioCaptureProps {
  /** RMS readouts from the clap-detector worklet. Used by the debug strip. */
  onClapRms?: RmsListener;
  /** Fires once with the live AudioContext + source when the mic is open. */
  onMicReady?: (mic: MicReady) => void;
  /** Fires when the mic is closed (component unmount or audio status change). */
  onMicClose?: () => void;
}

/**
 * Owns the mic stream + the clap-detection worklet. The voice pipeline
 * (Phase 3) attaches its own worklet to the same `source` via the
 * `onMicReady` callback so we use one mic / one AudioContext for both
 * features.
 */
export function AudioCapture({ onClapRms, onMicReady, onMicClose }: AudioCaptureProps) {
  const audio = useJarvis((s) => s.audio);
  const wake = useJarvis((s) => s.wake);
  const setAudio = useJarvis((s) => s.setAudio);

  const micRef = useRef<MicSource | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const onClapRmsRef = useRef<RmsListener | undefined>(onClapRms);
  const onMicReadyRef = useRef<typeof onMicReady>(onMicReady);
  const onMicCloseRef = useRef<typeof onMicClose>(onMicClose);

  useEffect(() => {
    onClapRmsRef.current = onClapRms;
  }, [onClapRms]);
  useEffect(() => {
    onMicReadyRef.current = onMicReady;
  }, [onMicReady]);
  useEffect(() => {
    onMicCloseRef.current = onMicClose;
  }, [onMicClose]);

  // We "should be running" the mic stack whenever the user has agreed to
  // turn it on. Driving the effect off a stable boolean (instead of the
  // raw `audio` enum) means the effect runs ONCE when the user clicks
  // "Engage" — it doesn't tear down + re-mount when we ourselves flip the
  // status from "requesting" -> "ready" mid-effect.
  const shouldRun = audio === "requesting" || audio === "ready";

  useEffect(() => {
    if (!shouldRun) return;

    let cancelled = false;
    const detector = new DoubleClapDetector(DEFAULT_CLAP_PARAMS);

    (async () => {
      try {
        // Single mic, NS off so claps survive but otherwise default-friendly.
        // sample_rate=16000 hint is honoured by Chrome/Safari/Firefox in
        // recent versions; if the browser overrides it the server resamples.
        const mic = await openMic({ rawTransients: true, sampleRate: 16000 });
        if (cancelled) {
          await mic.release();
          return;
        }
        micRef.current = mic;

        await mic.context.audioWorklet.addModule(
          "/worklets/clap-detector.js",
        );
        if (cancelled) {
          await mic.release();
          return;
        }

        const node = new AudioWorkletNode(mic.context, "clap-detector", {
          numberOfInputs: 1,
          numberOfOutputs: 0,
          channelCount: 1,
          processorOptions: { windowSize: 1024 },
        });
        nodeRef.current = node;

        node.port.onmessage = (
          event: MessageEvent<{ rms: number; t: number }>,
        ) => {
          const { rms } = event.data;
          onClapRmsRef.current?.(rms);
          const fired = detector.feed(rms, performance.now());
          if (fired) wake("double-clap");
        };

        mic.source.connect(node);

        // Build a parallel AnalyserNode for the Three.js LISTENING
        // visualizer. AnalyserNodes are passive observers — adding one
        // doesn't affect the worklet path.
        const analyser = mic.context.createAnalyser();
        analyser.fftSize = 512;
        analyser.smoothingTimeConstant = 0.7;
        mic.source.connect(analyser);
        analyserRef.current = analyser;
        audioBus.setMicAnalyser(analyser);

        // Hand the AudioContext + source up so the voice pipeline can
        // attach its own worklet.
        onMicReadyRef.current?.({ context: mic.context, source: mic.source });

        // Mic is fully wired — flip the public status so the rest of the
        // app (HUD chip, MicPrompt, etc.) reflects "armed".
        setAudio("ready");
      } catch (err) {
        if (cancelled) return;
        const msg =
          err instanceof DOMException && err.name === "NotAllowedError"
            ? "Microphone permission denied."
            : err instanceof Error
              ? err.message
              : "Failed to start audio.";
        const status =
          err instanceof DOMException && err.name === "NotAllowedError"
            ? "denied"
            : "error";
        setAudio(status, msg);
      }
    })();

    return () => {
      cancelled = true;
      try {
        nodeRef.current?.disconnect();
      } catch {
        /* already disconnected */
      }
      nodeRef.current = null;
      try {
        analyserRef.current?.disconnect();
      } catch {
        /* already disconnected */
      }
      analyserRef.current = null;
      audioBus.setMicAnalyser(null);
      onMicCloseRef.current?.();
      void micRef.current?.release();
      micRef.current = null;
    };
  }, [shouldRun, wake, setAudio]);

  return null;
}
