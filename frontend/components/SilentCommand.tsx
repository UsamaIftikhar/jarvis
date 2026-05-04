"use client";

/**
 * Silent command input — press "/" or Cmd+K to open a slim HUD-styled text
 * bar at the bottom of the screen. Type a command, hit Enter. JARVIS speaks
 * the answer; the bar auto-closes. Escape dismisses without sending.
 *
 * Sends {"type":"text_input","text":string} over the WebSocket — no mic,
 * no audio capture, no visible speech. Ideal when around other people.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import type { JarvisWsClient } from "@/lib/wsClient";
import { useJarvis } from "@/lib/state";

interface Props {
  wsRef: React.RefObject<JarvisWsClient | null>;
}

export function SilentCommand({ wsRef }: Props) {
  const [open, setOpen]     = useState(false);
  const [text, setText]     = useState("");
  const [sent, setSent]     = useState(false);
  const inputRef            = useRef<HTMLInputElement>(null);
  const wakeText            = useJarvis((s) => s.wakeText);
  const state               = useJarvis((s) => s.state);

  const openBar = useCallback(() => {
    setOpen(true);
    setSent(false);
    setText("");
  }, []);

  const closeBar = useCallback(() => {
    setOpen(false);
    setText("");
    setSent(false);
  }, []);

  const submit = useCallback(() => {
    const trimmed = text.trim();
    if (!trimmed) { closeBar(); return; }
    const ws = wsRef.current;
    if (!ws || !ws.isOpen()) return;
    ws.send({ type: "text_input", text: trimmed });
    wakeText();
    setSent(true);
    // Auto-close after a brief "sent" confirmation flash
    setTimeout(closeBar, 800);
  }, [text, wsRef, wakeText, closeBar]);

  // Global keyboard: "/" or Cmd+K to open, Escape to close
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Don't steal "/" while typing in something else
      const tag = (e.target as HTMLElement)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA") return;

      if (e.key === "/" && !open) {
        e.preventDefault();
        openBar();
        return;
      }
      if ((e.metaKey || e.ctrlKey) && e.key === "k") {
        e.preventDefault();
        open ? closeBar() : openBar();
        return;
      }
      if (e.key === "Escape" && open) {
        closeBar();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, openBar, closeBar]);

  // Focus input when bar opens
  useEffect(() => {
    if (open) {
      setTimeout(() => inputRef.current?.focus(), 50);
    }
  }, [open]);

  // Don't block user interaction while JARVIS is already speaking/thinking
  const blocked = state === "THINKING" || state === "SPEAKING";

  return (
    <>
      {/* Subtle hint icon — bottom center, only in IDLE */}
      {!open && state === "IDLE" && (
        <button
          type="button"
          className="pointer-events-auto fixed bottom-14 left-1/2 -translate-x-1/2 z-20
                     font-display text-[8px] uppercase tracking-[0.4em] text-jarvis-muted/40
                     hover:text-jarvis-muted/70 transition-colors duration-200"
          onClick={openBar}
          title="Silent command (press /)"
        >
          ⌨ type
        </button>
      )}

      {/* Input bar */}
      {open && (
        <div
          className="pointer-events-auto fixed inset-x-0 bottom-16 z-30 flex justify-center px-6 animate-slide-up"
        >
          <div
            className="relative w-full max-w-2xl"
            style={{
              background: "rgba(8, 14, 20, 0.92)",
              border: sent
                ? "1px solid rgba(0,255,255,0.6)"
                : "1px solid rgba(0,212,255,0.35)",
              boxShadow: sent
                ? "0 0 18px rgba(0,255,255,0.25), inset 0 0 12px rgba(0,255,255,0.04)"
                : "0 0 14px rgba(0,212,255,0.15), inset 0 0 8px rgba(0,212,255,0.03)",
              borderRadius: 2,
              transition: "border-color 0.2s, box-shadow 0.2s",
            }}
          >
            {/* Top accent line */}
            <div
              className="absolute inset-x-0 top-0 h-px"
              style={{
                background: sent
                  ? "linear-gradient(to right, transparent, rgba(0,255,255,0.7), transparent)"
                  : "linear-gradient(to right, transparent, rgba(0,212,255,0.5), transparent)",
              }}
            />

            <div className="flex items-center gap-3 px-4 py-3">
              {/* Mode label */}
              <span className="font-display text-[9px] uppercase tracking-[0.45em] text-jarvis-muted/60 shrink-0">
                {sent ? "SENT" : "CMD"}
              </span>

              <div className="w-px h-3 bg-jarvis-blue/20" />

              {/* Text input */}
              <input
                ref={inputRef}
                type="text"
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") { e.preventDefault(); submit(); }
                  if (e.key === "Escape") { e.preventDefault(); closeBar(); }
                }}
                disabled={sent || blocked}
                placeholder={blocked ? "JARVIS is busy…" : "type a command…"}
                className="flex-1 bg-transparent font-body text-sm text-jarvis-text/90
                           placeholder:text-jarvis-muted/30 outline-none caret-jarvis-cyan
                           disabled:opacity-50"
                spellCheck={false}
                autoComplete="off"
              />

              {/* Character count / send hint */}
              {text.length > 0 && !sent && (
                <span className="font-display text-[8px] text-jarvis-muted/40 shrink-0">
                  ↵
                </span>
              )}
              {sent && (
                <span
                  className="font-display text-[8px] uppercase tracking-[0.3em] text-jarvis-cyan shrink-0"
                  style={{ textShadow: "0 0 8px rgba(0,255,255,0.6)" }}
                >
                  ✓
                </span>
              )}
            </div>

            {/* Bottom accent line */}
            <div
              className="absolute inset-x-0 bottom-0 h-px"
              style={{
                background: "linear-gradient(to right, transparent, rgba(0,212,255,0.2), transparent)",
              }}
            />

            {/* Corner ticks */}
            <div className="absolute top-0 left-0 w-2 h-2 border-t border-l border-jarvis-blue/50" />
            <div className="absolute top-0 right-0 w-2 h-2 border-t border-r border-jarvis-blue/50" />
            <div className="absolute bottom-0 left-0 w-2 h-2 border-b border-l border-jarvis-blue/50" />
            <div className="absolute bottom-0 right-0 w-2 h-2 border-b border-r border-jarvis-blue/50" />
          </div>
        </div>
      )}
    </>
  );
}
