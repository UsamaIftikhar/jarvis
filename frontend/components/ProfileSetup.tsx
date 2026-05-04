"use client";

/**
 * One-time profile capture when the backend has no user name yet.
 * POSTs to FastAPI `/profile/setup` then hides itself.
 */

import { useCallback, useState } from "react";
import { getJarvisHttpBase } from "@/lib/jarvisEndpoints";
import { useJarvis } from "@/lib/state";

export function ProfileSetup() {
  const needs = useJarvis((s) => s.needsProfileSetup);
  const setNeeds = useJarvis((s) => s.setNeedsProfileSetup);
  const [name, setName] = useState("");
  const [city, setCity] = useState("");
  const [topics, setTopics] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const submit = useCallback(async () => {
    if (!name.trim()) {
      setErr("A name is required, sir.");
      return;
    }
    setBusy(true);
    setErr(null);
    try {
      const news_topics = topics
        .split(",")
        .map((t) => t.trim())
        .filter(Boolean);
      const r = await fetch(`${getJarvisHttpBase()}/profile/setup`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: name.trim(),
          city: city.trim(),
          news_topics,
        }),
      });
      if (!r.ok) {
        throw new Error(await r.text());
      }
      setNeeds(false);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Setup failed.");
    } finally {
      setBusy(false);
    }
  }, [name, city, topics, setNeeds]);

  if (!needs) return null;

  return (
    <div className="pointer-events-auto fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6">
      <div className="max-w-md border border-jarvis-cyan/40 bg-jarvis-bg/95 p-6 font-body text-jarvis-text shadow-[0_0_40px_rgba(0,255,255,0.15)]">
        <h2 className="font-orbitron text-lg tracking-widest text-jarvis-cyan">
          INITIALISE PROFILE
        </h2>
        <p className="mt-2 text-sm opacity-80">
          JARVIS learns faster with a few basics. You can change these later in
          the profile file on disk.
        </p>
        <label className="mt-4 block text-xs uppercase tracking-wider opacity-70">
          What should I call you?
        </label>
        <input
          className="mt-1 w-full border border-white/20 bg-black/40 px-3 py-2 text-sm outline-none focus:border-jarvis-cyan"
          value={name}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <label className="mt-3 block text-xs uppercase tracking-wider opacity-70">
          Your city (weather / time context)
        </label>
        <input
          className="mt-1 w-full border border-white/20 bg-black/40 px-3 py-2 text-sm outline-none focus:border-jarvis-cyan"
          value={city}
          onChange={(e) => setCity(e.target.value)}
        />
        <label className="mt-3 block text-xs uppercase tracking-wider opacity-70">
          Preferred news topics (comma-separated, optional)
        </label>
        <input
          className="mt-1 w-full border border-white/20 bg-black/40 px-3 py-2 text-sm outline-none focus:border-jarvis-cyan"
          value={topics}
          onChange={(e) => setTopics(e.target.value)}
          placeholder="e.g. AI, markets, Pakistan"
        />
        {err && <p className="mt-2 text-sm text-red-400">{err}</p>}
        <button
          type="button"
          className="mt-6 w-full border border-jarvis-cyan/60 bg-jarvis-cyan/10 py-2 font-orbitron text-sm tracking-widest text-jarvis-cyan hover:bg-jarvis-cyan/20 disabled:opacity-50"
          onClick={() => void submit()}
          disabled={busy}
        >
          {busy ? "SAVING…" : "COMMIT"}
        </button>
      </div>
    </div>
  );
}
