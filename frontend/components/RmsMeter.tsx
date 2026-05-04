"use client";

import { useEffect, useRef, useState } from "react";
import { DEFAULT_CLAP_PARAMS } from "@/lib/clapDetector";

/**
 * Tiny live RMS readout — only useful when tuning the clap threshold.
 * Rendered when the URL contains `?debug=1`.
 *
 * The parent `<AudioCapture onRms={...} />` calls `attach(handler)` on
 * mount; we use a ref-pattern so we don't re-render on every audio frame
 * (47 Hz would tank performance otherwise).
 */
export function RmsMeter({ register }: { register: (h: (rms: number) => void) => () => void }) {
  const [peak, setPeak] = useState(0);
  const [now, setNow] = useState(0);
  const peakRef = useRef(0);

  useEffect(() => {
    const unregister = register((rms) => {
      // Track the running peak (decays each animation frame below).
      if (rms > peakRef.current) peakRef.current = rms;
      // We update React state via rAF to avoid 47 setState/sec.
    });
    let raf = 0;
    const tick = () => {
      raf = requestAnimationFrame(tick);
      setPeak(peakRef.current);
      setNow((prev) => {
        // Light EMA so the live value is smoothed and human-readable.
        const decayed = peakRef.current * 0.6 + prev * 0.4;
        peakRef.current *= 0.92; // peak decays
        return decayed;
      });
    };
    raf = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(raf);
      unregister();
    };
  }, [register]);

  const threshold = DEFAULT_CLAP_PARAMS.threshold;
  const pctNow = Math.min(100, (now / (threshold * 4)) * 100);
  const pctThreshold = Math.min(100, (threshold / (threshold * 4)) * 100);
  const pctPeak = Math.min(100, (peak / (threshold * 4)) * 100);

  return (
    <div className="pointer-events-none fixed bottom-20 left-1/2 z-10 -translate-x-1/2 font-display text-[10px] uppercase tracking-[0.3em] text-jarvis-muted">
      <div className="mb-1 flex justify-between">
        <span>rms</span>
        <span className="text-jarvis-blue">
          {now.toFixed(3)} | peak {peak.toFixed(3)}
        </span>
      </div>
      <div className="relative h-1 w-72 bg-jarvis-blue/15">
        <div
          className="absolute inset-y-0 left-0 bg-jarvis-blue/80"
          style={{ width: `${pctNow}%` }}
        />
        <div
          className="absolute inset-y-0 left-0 w-px bg-jarvis-cyan/80"
          style={{ left: `${pctThreshold}%` }}
        />
        <div
          className="absolute -top-0.5 h-2 w-px bg-jarvis-text/80"
          style={{ left: `${pctPeak}%` }}
        />
      </div>
      <div className="mt-1 text-[9px] tracking-[0.4em]">
        threshold {threshold.toFixed(2)}
      </div>
    </div>
  );
}
