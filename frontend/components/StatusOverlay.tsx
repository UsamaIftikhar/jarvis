"use client";

import { useEffect, useRef, useState } from "react";
import { useJarvis, type JarvisState, type WakeSource } from "@/lib/state";

const STATE_LABEL: Record<JarvisState, string> = {
  IDLE:      "STANDBY",
  LISTENING: "LISTENING",
  THINKING:  "PROCESSING",
  SPEAKING:  "RESPONDING",
  ERROR:     "OFFLINE",
};

const STATE_COLOR: Record<JarvisState, string> = {
  IDLE:      "text-jarvis-blue",
  LISTENING: "text-jarvis-cyan",
  THINKING:  "text-jarvis-gold",
  SPEAKING:  "text-jarvis-cyan",
  ERROR:     "text-jarvis-danger",
};

const STATE_GLOW: Record<JarvisState, string> = {
  IDLE:      "0 0 16px rgba(0,212,255,0.7),  0 0 40px rgba(0,212,255,0.3)",
  LISTENING: "0 0 16px rgba(0,255,255,0.9),  0 0 40px rgba(0,255,255,0.45)",
  THINKING:  "0 0 16px rgba(255,192,68,0.8), 0 0 40px rgba(255,192,68,0.3)",
  SPEAKING:  "0 0 16px rgba(0,255,255,0.9),  0 0 40px rgba(0,255,255,0.45)",
  ERROR:     "0 0 16px rgba(255,58,85,0.9),  0 0 40px rgba(255,58,85,0.4)",
};

const IDLE_HINT = 'Say "Hey JARVIS" or press Space';

export function StatusOverlay() {
  const state           = useJarvis((s) => s.state);
  const connection      = useJarvis((s) => s.connection);
  const audio           = useJarvis((s) => s.audio);
  const lastWake        = useJarvis((s) => s.lastWake);
  const lastMessage     = useJarvis((s) => s.lastMessage);
  const errorMessage    = useJarvis((s) => s.errorMessage);
  const transcript      = useJarvis((s) => s.transcript);
  const response        = useJarvis((s) => s.response);
  const proactiveAlert  = useJarvis((s) => s.proactiveAlert);
  const thinkingStep    = useJarvis((s) => s.thinkingStep);
  const setProactiveAlert = useJarvis((s) => s.setProactiveAlert);

  const isError = state === "ERROR";

  useEffect(() => {
    if (!proactiveAlert) return;
    const ms = proactiveAlert.priority === "low" ? 10_000 : 60_000;
    const t  = setTimeout(() => setProactiveAlert(null), ms);
    return () => clearTimeout(t);
  }, [proactiveAlert, setProactiveAlert]);

  return (
    <div className="pointer-events-none fixed inset-0 z-10 select-none">

      {/* Scanlines */}
      <div className="scanlines absolute inset-0 opacity-50" />
      {/* Moving scan beam */}
      <div className="scan-beam" />

      {/* Wake flash */}
      {lastWake && <WakeFlash key={lastWake.ts} source={lastWake.source} />}

      {/* ── TOP BAR ─────────────────────────────────────────────── */}
      <div className="absolute inset-x-0 top-0 flex items-start justify-between px-6 pt-5">

        {/* Top-left: brand */}
        <div className="hud-bracket px-3 py-2">
          <div className="font-display text-sm font-bold tracking-[0.45em] text-jarvis-blue glow-blue">
            J.A.R.V.I.S.
          </div>
          <div className="mt-0.5 font-display text-[9px] tracking-[0.35em] text-jarvis-muted">
            JUST A RATHER VERY INTELLIGENT SYSTEM
          </div>
          <div className="mt-1 font-display text-[9px] tracking-[0.3em] text-jarvis-muted/60">
            CORE v0.6 · BUILD 20260502
          </div>
        </div>

        {/* Top-right: status chips */}
        <div className="flex flex-col items-end gap-1.5 pt-1">
          <StatusChip
            label="NET LINK"
            value={connection.toUpperCase()}
            color={connection === "connected" ? "blue" : connection === "connecting" ? "yellow" : "red"}
          />
          <StatusChip
            label="MIC"
            value={audioLabel(audio)}
            color={audio === "ready" ? "blue" : audio === "requesting" ? "yellow" : audio === "idle" ? "muted" : "red"}
          />
          <StatusChip
            label="AI CORE"
            value={connection === "connected" ? "ONLINE" : "OFFLINE"}
            color={connection === "connected" ? "blue" : "muted"}
          />
        </div>
      </div>

      {/* ── LEFT PANEL ──────────────────────────────────────────── */}
      <LeftPanel state={state} />

      {/* ── RIGHT PANEL ─────────────────────────────────────────── */}
      <RightPanel connection={connection} audio={audio} />

      {/* ── CENTER: Transcript (spoken query) ───────────────────── */}
      {transcript && state !== "IDLE" && (
        <div className="absolute inset-x-0 top-20 flex justify-center px-40 animate-slide-up">
          <div className="hud-bracket max-w-2xl px-5 py-2 text-center">
            <div className="font-display text-[9px] uppercase tracking-[0.5em] text-jarvis-muted">
              ◈ input received
            </div>
            <div className="mt-1 font-body text-sm leading-snug text-jarvis-cyan glow-cyan">
              {transcript}
            </div>
          </div>
        </div>
      )}

      {/* ── CENTER: Thinking step label ─────────────────────────── */}
      {state === "THINKING" && thinkingStep && (
        <div className="absolute inset-x-0 bottom-44 flex justify-center animate-fade-in">
          <div className="flex items-center gap-2 font-display text-[10px] uppercase tracking-[0.4em] text-jarvis-gold">
            <ThinkingDots />
            {thinkingStep}
          </div>
        </div>
      )}

      {/* ── CENTER: State badge ──────────────────────────────────── */}
      <div className="absolute inset-x-0 bottom-28 flex flex-col items-center gap-1.5">
        <div className="font-display text-[9px] uppercase tracking-[0.6em] text-jarvis-muted">
          · mode ·
        </div>
        <div
          className={`animate-flicker font-display text-[22px] font-bold uppercase tracking-[0.6em] ${STATE_COLOR[state]}`}
          style={{ textShadow: STATE_GLOW[state] }}
        >
          {STATE_LABEL[state]}
        </div>
        <StateDashes state={state} />
      </div>

      {/* ── Proactive alert ─────────────────────────────────────── */}
      {proactiveAlert && (
        <div className="pointer-events-auto absolute inset-x-0 bottom-20 z-20 flex justify-center px-6 animate-slide-up">
          <button
            type="button"
            className={`hud-bracket max-w-2xl px-4 py-2 text-center font-body text-xs ${
              proactiveAlert.priority === "high" ? "text-jarvis-cyan" : "text-jarvis-muted"
            }`}
            onClick={() => setProactiveAlert(null)}
          >
            <span className="font-display text-[9px] uppercase tracking-[0.4em]">
              ⚡ alert · {proactiveAlert.priority}
            </span>
            {proactiveAlert.title && (
              <div className="mt-1 font-display text-[10px] uppercase tracking-[0.35em] text-jarvis-cyan/90">
                {proactiveAlert.title}
              </div>
            )}
            <div className="mt-1 text-sm">{proactiveAlert.message}</div>
            <span className="mt-1 block text-[9px] opacity-40">tap to dismiss</span>
          </button>
        </div>
      )}

      {/* ── BOTTOM: streaming response / last message ───────────── */}
      <div className="absolute inset-x-0 bottom-5 flex justify-center px-40">
        <div className="hud-bracket max-w-3xl w-full px-5 py-2 text-center font-body text-sm text-jarvis-text/85">
          <BottomLine
            state={state}
            isError={isError}
            errorMessage={errorMessage}
            response={response}
            lastMessage={lastMessage}
          />
        </div>
      </div>

      {/* ── Corner labels ───────────────────────────────────────── */}
      <div className="absolute bottom-5 left-6 font-display text-[9px] uppercase tracking-[0.3em] text-jarvis-muted/60">
        sys.audio · {audio === "ready" ? "armed" : "idle"}
      </div>
      <div className="absolute bottom-5 right-6 font-display text-[9px] uppercase tracking-[0.3em] text-jarvis-muted/60">
        sys.core · {connection === "connected" ? "nominal" : "standby"}
      </div>
    </div>
  );
}

// ── Sub-components ────────────────────────────────────────────────────

function BottomLine({
  state,
  isError,
  errorMessage,
  response,
  lastMessage,
}: {
  state: JarvisState;
  isError: boolean;
  errorMessage: string | null;
  response: string;
  lastMessage: string | null;
}) {
  if (isError && errorMessage)
    return <span className="text-jarvis-danger">{errorMessage}</span>;
  if (response.trim().length > 0)
    return <span className={state === "SPEAKING" ? "typing-cursor" : ""}>{response}</span>;
  if (lastMessage)
    return <span className="text-jarvis-text/70">{lastMessage}</span>;
  return <span className="text-jarvis-muted/60">{IDLE_HINT}</span>;
}

function ThinkingDots() {
  const [dots, setDots] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setDots((d) => (d + 1) % 4), 350);
    return () => clearInterval(id);
  }, []);
  return <span className="text-jarvis-gold/70">{["·", "··", "···", "····"][dots]}</span>;
}

function StateDashes({ state }: { state: JarvisState }) {
  const colors: Record<JarvisState, string> = {
    IDLE:      "bg-jarvis-blue/40",
    LISTENING: "bg-jarvis-cyan/80",
    THINKING:  "bg-jarvis-gold/80",
    SPEAKING:  "bg-jarvis-cyan/80",
    ERROR:     "bg-jarvis-danger/80",
  };
  return (
    <div className="flex gap-1 mt-0.5">
      {Array.from({ length: 5 }).map((_, i) => (
        <div
          key={i}
          className={`h-px rounded-full transition-all duration-500 ${colors[state]}`}
          style={{
            width: i === 2 ? 24 : i === 1 || i === 3 ? 14 : 8,
            opacity: state === "IDLE" ? 0.5 : 1,
          }}
        />
      ))}
    </div>
  );
}

type ChipColor = "blue" | "yellow" | "red" | "muted";

function StatusChip({ label, value, color }: { label: string; value: string; color: ChipColor }) {
  const dotClass =
    color === "blue"   ? "bg-jarvis-blue pulse-dot shadow-glow-blue" :
    color === "yellow" ? "bg-yellow-400" :
    color === "red"    ? "bg-jarvis-danger pulse-dot" :
                         "bg-jarvis-muted";
  const valClass =
    color === "blue"   ? "text-jarvis-blue" :
    color === "yellow" ? "text-yellow-400" :
    color === "red"    ? "text-jarvis-danger" :
                         "text-jarvis-muted/60";
  return (
    <div className="flex items-center gap-2 font-display text-[9px] uppercase tracking-[0.3em]">
      <div className={`h-1.5 w-1.5 rounded-full ${dotClass}`} />
      <span className="text-jarvis-muted/50">{label}</span>
      <span className={valClass}>{value}</span>
    </div>
  );
}

function LeftPanel({ state }: { state: JarvisState }) {
  return (
    <div className="absolute left-5 top-1/2 -translate-y-1/2 flex flex-col gap-3 w-36">
      <PanelBlock title="NEURAL LINK">
        <TelemetryRow label="LATENCY"  value="12ms"   active={state !== "IDLE"} />
        <TelemetryRow label="TOKENS"   value="∞"      active={true} />
        <TelemetryRow label="CONTEXT"  value="128K"   active={true} />
      </PanelBlock>
      <div className="hud-divider" />
      <PanelBlock title="AUDIO I/O">
        <TelemetryRow label="SAMPLE"   value="16kHz"  active={true} />
        <TelemetryRow label="CODEC"    value="PCM16"  active={true} />
        <TelemetryRow label="STREAM"   value={state === "LISTENING" || state === "SPEAKING" ? "ACTIVE" : "IDLE"} active={state === "LISTENING" || state === "SPEAKING"} />
      </PanelBlock>
      <div className="hud-divider" />
      <PanelBlock title="COGNITIVE">
        <TelemetryRow label="MODELS"   value="V3"     active={true} />
        <TelemetryRow label="WHISPER"  value="SM.EN"  active={true} />
        <TelemetryRow label="TTS"      value="PIPER"  active={true} />
      </PanelBlock>
    </div>
  );
}

function RightPanel({ connection, audio }: { connection: string; audio: string }) {
  const [uptime, setUptime] = useState(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    const id = setInterval(() => {
      setUptime(Math.floor((Date.now() - startRef.current) / 1000));
    }, 1000);
    return () => clearInterval(id);
  }, []);

  const mins = Math.floor(uptime / 60).toString().padStart(2, "0");
  const secs = (uptime % 60).toString().padStart(2, "0");

  return (
    <div className="absolute right-5 top-1/2 -translate-y-1/2 flex flex-col gap-3 w-36 items-end">
      <PanelBlock title="SYSTEM" align="right">
        <TelemetryRow label="STATUS"  value={connection === "connected" ? "ONLINE" : "OFFLINE"} active={connection === "connected"} align="right" />
        <TelemetryRow label="UPTIME"  value={`${mins}:${secs}`}   active={true}                  align="right" />
        <TelemetryRow label="MODE"    value="ACTIVE"              active={true}                  align="right" />
      </PanelBlock>
      <div className="hud-divider" />
      <PanelBlock title="MEMORY" align="right">
        <TelemetryRow label="CHROMA"  value="READY"  active={true}  align="right" />
        <TelemetryRow label="RECALL"  value="ON"     active={true}  align="right" />
        <TelemetryRow label="TOOLS"   value="11"     active={true}  align="right" />
      </PanelBlock>
      <div className="hud-divider" />
      <PanelBlock title="PERIMETER" align="right">
        <TelemetryRow label="TAVILY"  value="LIVE"   active={true}  align="right" />
        <TelemetryRow label="CAL"     value={audio === "ready" ? "SYNC" : "IDLE"} active={audio === "ready"} align="right" />
        <TelemetryRow label="VISION"  value="ON"     active={true}  align="right" />
      </PanelBlock>
    </div>
  );
}

function PanelBlock({
  title,
  children,
  align = "left",
}: {
  title: string;
  children: React.ReactNode;
  align?: "left" | "right";
}) {
  return (
    <div className={`flex flex-col gap-1 ${align === "right" ? "items-end" : "items-start"}`}>
      <div className="font-display text-[8px] uppercase tracking-[0.4em] text-jarvis-muted/50 mb-0.5">
        {title}
      </div>
      {children}
    </div>
  );
}

function TelemetryRow({
  label,
  value,
  active,
  align = "left",
}: {
  label: string;
  value: string;
  active: boolean;
  align?: "left" | "right";
}) {
  return (
    <div className={`flex items-center gap-2 ${align === "right" ? "flex-row-reverse" : ""}`}>
      <span className="font-display text-[8px] tracking-[0.25em] text-jarvis-muted/45 uppercase">
        {label}
      </span>
      <span
        className={`font-display text-[8px] font-bold tracking-[0.2em] ${
          active ? "text-jarvis-blue animate-data-stream" : "text-jarvis-muted/40"
        }`}
      >
        {value}
      </span>
    </div>
  );
}

const WAKE_SOURCE_LABEL: Record<WakeSource, string> = {
  "double-clap": "DOUBLE CLAP",
  "hey-jarvis":  "WAKE WORD",
  manual:        "MANUAL",
};

function WakeFlash({ source }: { source: WakeSource }) {
  const [visible, setVisible] = useState(true);
  useEffect(() => {
    const id = window.setTimeout(() => setVisible(false), 1400);
    return () => window.clearTimeout(id);
  }, []);
  if (!visible) return null;
  return (
    <div className="pointer-events-none absolute inset-0 flex items-center justify-center">
      <div
        className="absolute inset-0"
        style={{
          background: "radial-gradient(ellipse at center, rgba(0,212,255,0.16) 0%, transparent 50%)",
          animation: "jarvis-wake-fade 1.4s ease-out forwards",
        }}
      />
      <div
        className="font-display text-[11px] uppercase tracking-[0.8em] text-jarvis-cyan glow-cyan"
        style={{ animation: "jarvis-wake-fade 1.4s ease-out forwards" }}
      >
        ◈ wake · {WAKE_SOURCE_LABEL[source]}
      </div>
      <style jsx>{`
        @keyframes jarvis-wake-fade {
          0%   { opacity: 0; transform: scale(0.92); }
          12%  { opacity: 1; transform: scale(1); }
          100% { opacity: 0; transform: scale(1.06); }
        }
      `}</style>
    </div>
  );
}

function audioLabel(audio: string): string {
  switch (audio) {
    case "idle":      return "IDLE";
    case "requesting": return "REQ…";
    case "ready":     return "ARMED";
    case "denied":    return "DENIED";
    case "error":     return "FAULT";
    default:          return "—";
  }
}
