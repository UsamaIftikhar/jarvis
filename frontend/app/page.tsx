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
import { BackendSettings } from "@/components/BackendSettings";
import { useJarvis } from "@/lib/state";
import { JarvisWsClient } from "@/lib/wsClient";
import { fetchProfileNeedsSetup, getJarvisWsUrl } from "@/lib/jarvisEndpoints";
import { VoicePipeline, setVoicePipelineDebug } from "@/lib/voicePipeline";
import { isNativePlatform } from "@/lib/nativeBridge";

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
  const [showSettings, setShowSettings] = useState(false);
  const [wsUrl, setWsUrl] = useState("");
  const [isNative, setIsNative] = useState(false);

  useEffect(() => {
    setWsUrl(getJarvisWsUrl());
    setIsNative(isNativePlatform());
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

  useEffect(() => {
    if (!wsUrl) return;
    setConnection("connecting");
    const pipeline = new VoicePipeline();
    pipelineRef.current = pipeline;

    const client = new JarvisWsClient(wsUrl, {
      onOpen: () => {
        setConnection("connected");
        setError(null);
        client.send({ type: "ping", ts: Date.now() });
        void fetchProfileNeedsSetup().then((needs) => {
          if (needs) setNeedsProfileSetup(true);
        });
        const mic = micRef.current;
        if (mic) {
          void pipeline.arm({ context: mic.context, source: mic.source, ws: client });
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
        if (msg.type === "thinking_step") { setThinkingStep(msg.label); return; }
        if (msg.type === "proactive_alert") {
          setProactiveAlert({ message: msg.message, priority: msg.priority });
          return;
        }
        if (msg.type === "reminder") {
          setProactiveAlert({ title: msg.name, message: msg.message, priority: "high" });
          return;
        }
        switch (msg.type) {
          case "pong": case "echo": break;
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
  }, [wsUrl, setConnection, setError, setLastMessage, setNeedsProfileSetup, setProactiveAlert, setThinkingStep]);

  const onMicReady = useCallback((mic: MicReady) => {
    micRef.current = mic;
    const ws = wsRef.current;
    const pipeline = pipelineRef.current;
    if (ws && pipeline && ws.isOpen()) {
      void pipeline.arm({ context: mic.context, source: mic.source, ws });
    }
  }, []);

  const onMicClose = useCallback(() => {
    micRef.current = null;
    void pipelineRef.current?.disarm();
  }, []);

  useEffect(() => {
    if (state !== "LISTENING") return;
    void pipelineRef.current?.startRecording();
  }, [state]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;
      if (e.code === "Space" && !e.repeat) { e.preventDefault(); wake("manual"); }
      else if (e.code === "Escape") { sleep(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [wake, sleep]);

  const onCanvasClick = useCallback(() => wake("manual"), [wake]);

  const handlersRef = useRef(new Set<(rms: number) => void>());
  const onClapRms = useCallback((rms: number) => {
    handlersRef.current.forEach((h) => h(rms));
  }, []);
  const register = useMemo(() => (h: (rms: number) => void) => {
    handlersRef.current.add(h);
    return () => { handlersRef.current.delete(h); };
  }, []);

  function handleSettingsSave() {
    // Reconnect with new URL
    setWsUrl(getJarvisWsUrl());
    setShowSettings(false);
  }

  return (
    <main className="relative h-screen w-screen overflow-hidden" onClick={onCanvasClick}>
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

      {/* Settings gear — visible on mobile / native */}
      {isNative && (
        <button
          onClick={(e) => { e.stopPropagation(); setShowSettings(true); }}
          className="fixed bottom-6 right-6 z-40 flex h-10 w-10 items-center justify-center rounded-full border border-cyan-900 bg-black/60 font-mono text-lg text-cyan-800 backdrop-blur transition hover:border-cyan-600 hover:text-cyan-500"
          aria-label="Settings"
        >
          ⚙
        </button>
      )}

      {showSettings && (
        <BackendSettings onClose={handleSettingsSave} />
      )}
    </main>
  );
}
