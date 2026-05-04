"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { JarvisCanvas } from "@/components/JarvisCanvas";
import { StatusOverlay } from "@/components/StatusOverlay";
import { AudioCapture, type MicReady } from "@/components/AudioCapture";
import { WakeDetector } from "@/components/WakeDetector";
import { MicPrompt } from "@/components/MicPrompt";
import { RmsMeter } from "@/components/RmsMeter";
import { ProfileSetup } from "@/components/ProfileSetup";
import { SilentCommand } from "@/components/SilentCommand";
import { useJarvis } from "@/lib/state";
import { JarvisWsClient } from "@/lib/wsClient";
import { fetchProfileNeedsSetup, getJarvisWsUrl } from "@/lib/jarvisEndpoints";
import { VoicePipeline, setVoicePipelineDebug } from "@/lib/voicePipeline";

const WS_URL = getJarvisWsUrl();

export default function Page() {
  const setConnection = useJarvis((s) => s.setConnection);
  const setLastMessage = useJarvis((s) => s.setLastMessage);
  const setError = useJarvis((s) => s.setError);
  const setThinkingStep = useJarvis((s) => s.setThinkingStep);
  const setProactiveAlert = useJarvis((s) => s.setProactiveAlert);
  const setNeedsProfileSetup = useJarvis((s) => s.setNeedsProfileSetup);
  const wake = useJarvis((s) => s.wake);
  const sleep = useJarvis((s) => s.sleep);
  const state = useJarvis((s) => s.state);

  const wsRef = useRef<JarvisWsClient | null>(null);
  const pipelineRef = useRef<VoicePipeline | null>(null);
  const micRef = useRef<MicReady | null>(null);

  const [debug, setDebug] = useState(false);

  useEffect(() => {
    if (typeof window === "undefined") return;
    const params = new URLSearchParams(window.location.search);
    const isDebug = params.get("debug") === "1";
    setDebug(isDebug);
    setVoicePipelineDebug(isDebug);
  }, []);

  useEffect(() => {
    void fetchProfileNeedsSetup().then((needs) => {
      if (needs) setNeedsProfileSetup(true);
    });
  }, [setNeedsProfileSetup]);

  // --- WebSocket lifecycle (Phase 1) + voice-pipeline routing (Phase 3) ----
  useEffect(() => {
    setConnection("connecting");
    const pipeline = new VoicePipeline();
    pipelineRef.current = pipeline;

    const client = new JarvisWsClient(WS_URL, {
      onOpen: () => {
        setConnection("connected");
        setError(null);
        client.send({ type: "ping", ts: Date.now() });
        void fetchProfileNeedsSetup().then((needs) => {
          if (needs) setNeedsProfileSetup(true);
        });
        // If the mic was already ready when the WS came up, arm now.
        const mic = micRef.current;
        if (mic) {
          console.log("[page] WS open + mic ready → arming pipeline");
          void pipeline.arm({
            context: mic.context,
            source: mic.source,
            ws: client,
          });
        }
      },
      onClose: () => setConnection("disconnected"),
      onError: () => setConnection("disconnected"),
      onMessage: (msg) => {
        if (msg.type === "hello") {
          setLastMessage(`Linked to ${msg.server} (${msg.version}).`);
          if (msg.needs_profile_setup) setNeedsProfileSetup(true);
          return;
        }
        if (msg.type === "thinking_step") {
          setThinkingStep(msg.label);
          return;
        }
        if (msg.type === "proactive_alert") {
          setProactiveAlert({
            message: msg.message,
            priority: msg.priority,
          });
          return;
        }
        if (msg.type === "reminder") {
          setProactiveAlert({
            title: msg.name,
            message: msg.message,
            priority: "high",
          });
          return;
        }
        switch (msg.type) {
          case "pong":
          case "echo":
            break;
          case "thinking":
            setThinkingStep(null);
            pipeline.handleServerMessage(msg);
            break;
          case "token":
            setThinkingStep(null);
            pipeline.handleServerMessage(msg);
            break;
          case "error":
            setError(msg.message);
            pipeline.handleServerMessage(msg);
            break;
          default:
            pipeline.handleServerMessage(msg);
            break;
        }
      },
      onBinary: (data) => pipeline.handleServerBinary(data),
    });
    client.connect();
    wsRef.current = client;

    return () => {
      void pipeline.disarm();
      client.close();
      wsRef.current = null;
      pipelineRef.current = null;
    };
  }, [
    setConnection,
    setError,
    setLastMessage,
    setNeedsProfileSetup,
    setProactiveAlert,
    setThinkingStep,
  ]);

  // --- Mic ready -> arm pipeline -------------------------------------------
  const onMicReady = useCallback((mic: MicReady) => {
    micRef.current = mic;
    const ws = wsRef.current;
    const pipeline = pipelineRef.current;
    const wsOpen = !!ws && ws.isOpen();
    console.log(
      `[page] onMicReady fired (pipeline=${!!pipeline}, ws=${!!ws}, ws.isOpen=${wsOpen})`,
    );
    if (ws && pipeline && wsOpen) {
      void pipeline.arm({ context: mic.context, source: mic.source, ws });
    } else if (ws && pipeline && !wsOpen) {
      // WS isn't open yet — onOpen above will pick up micRef.current and arm.
      console.log("[page] mic ready but WS not open yet — deferring arm()");
    }
  }, []);

  const onMicClose = useCallback(() => {
    micRef.current = null;
    void pipelineRef.current?.disarm();
  }, []);

  // --- LISTENING -> start streaming PCM ------------------------------------
  // The state machine is the source of truth: when state flips to LISTENING
  // (from any wake source) we start recording. The pipeline itself drives
  // the LISTENING -> THINKING -> SPEAKING -> IDLE transitions from there.
  useEffect(() => {
    if (state !== "LISTENING") return;
    void pipelineRef.current?.startRecording();
  }, [state]);

  // --- Manual wake (Space / click) -----------------------------------------
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.code === "Space" && !e.repeat) {
        e.preventDefault();
        wake("manual");
      } else if (e.code === "Escape") {
        sleep();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wake, sleep]);

  const onCanvasClick = useCallback(() => wake("manual"), [wake]);

  // --- Debug RMS routing ---------------------------------------------------
  const handlersRef = useRef(new Set<(rms: number) => void>());
  const onClapRms = useCallback((rms: number) => {
    handlersRef.current.forEach((h) => h(rms));
  }, []);
  const register = useMemo(
    () => (h: (rms: number) => void) => {
      handlersRef.current.add(h);
      return () => {
        handlersRef.current.delete(h);
      };
    },
    [],
  );

  return (
    <main
      className="relative h-screen w-screen overflow-hidden"
      onClick={onCanvasClick}
    >
      <JarvisCanvas />
      <ProfileSetup />
      <StatusOverlay />
      <MicPrompt />
      <AudioCapture
        onClapRms={debug ? onClapRms : undefined}
        onMicReady={onMicReady}
        onMicClose={onMicClose}
      />
      <WakeDetector />
      <SilentCommand wsRef={wsRef} />
      {debug && <RmsMeter register={register} />}
    </main>
  );
}
