"use client";

import { useState, useEffect } from "react";
import {
  getJarvisHttpBase,
  saveBackendUrl,
  clearBackendUrl,
} from "@/lib/jarvisEndpoints";

export function BackendSettings({ onClose }: { onClose: () => void }) {
  const [url, setUrl] = useState("");
  const [status, setStatus] = useState<"idle" | "testing" | "ok" | "fail">("idle");

  useEffect(() => {
    setUrl(getJarvisHttpBase());
  }, []);

  async function testAndSave() {
    const clean = url.trim().replace(/\/$/, "");
    if (!clean) return;
    setStatus("testing");
    try {
      const r = await fetch(`${clean}/health`, {
        signal: AbortSignal.timeout(4000),
      });
      if (r.ok) {
        saveBackendUrl(clean);
        setStatus("ok");
        setTimeout(onClose, 800);
      } else {
        setStatus("fail");
      }
    } catch {
      setStatus("fail");
    }
  }

  function reset() {
    clearBackendUrl();
    setUrl("http://localhost:8000");
    setStatus("idle");
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 backdrop-blur-sm">
      <div className="w-[90vw] max-w-sm rounded-xl border border-cyan-900 bg-black p-6 shadow-2xl">
        <h2 className="mb-4 text-center font-mono text-sm tracking-widest text-cyan-400">
          BACKEND CONNECTION
        </h2>
        <p className="mb-3 text-center font-mono text-xs text-cyan-700">
          Enter your Mac&apos;s Tailscale IP address
        </p>
        <input
          className="w-full rounded border border-cyan-800 bg-black px-3 py-2 font-mono text-sm text-cyan-300 outline-none focus:border-cyan-500"
          placeholder="http://100.x.x.x:8000"
          value={url}
          onChange={(e) => { setUrl(e.target.value); setStatus("idle"); }}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
        />
        <div className="mt-2 h-4 text-center font-mono text-xs">
          {status === "testing" && <span className="text-cyan-600">connecting…</span>}
          {status === "ok" && <span className="text-green-400">connected ✓</span>}
          {status === "fail" && <span className="text-red-400">unreachable — check IP and backend</span>}
        </div>
        <div className="mt-4 flex gap-2">
          <button
            onClick={testAndSave}
            className="flex-1 rounded border border-cyan-600 py-2 font-mono text-xs text-cyan-400 transition hover:bg-cyan-900/30"
          >
            CONNECT
          </button>
          <button
            onClick={reset}
            className="rounded border border-cyan-900 px-3 py-2 font-mono text-xs text-cyan-800 transition hover:border-cyan-700 hover:text-cyan-600"
          >
            RESET
          </button>
          <button
            onClick={onClose}
            className="rounded border border-cyan-900 px-3 py-2 font-mono text-xs text-cyan-800 transition hover:border-cyan-700 hover:text-cyan-600"
          >
            ✕
          </button>
        </div>
        <p className="mt-4 text-center font-mono text-xs text-cyan-900">
          Install Tailscale on Mac + iPhone to get the IP
        </p>
      </div>
    </div>
  );
}
