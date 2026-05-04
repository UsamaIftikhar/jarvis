/**
 * Double-clap detector — pure logic (no DOM / no AudioContext deps) so it
 * stays unit-testable. Drive it by feeding `feed(rms, ts)` for each RMS
 * reading from the AudioWorklet; it returns `true` exactly once per
 * detected double-clap.
 *
 * Spec: RMS > 0.15, two peaks within 800 ms, gap > 100 ms.
 *
 * Implementation notes:
 *   - "Peak" = rising-edge crossing of the threshold. Using rising edges
 *     means sustained noise above the threshold won't keep firing peaks.
 *   - The 100 ms minimum gap doubles as the natural refractory: any second
 *     edge inside 100 ms (i.e. echoes / chatter from the same physical
 *     clap) is silently ignored. Edges between 100 and 800 ms after the
 *     first peak fire the wake event. Edges later than 800 ms restart the
 *     window with a fresh "first peak".
 */

export interface ClapDetectorParams {
  threshold: number; // RMS above which we consider the signal "loud"
  minGapMs: number; // minimum time between the two peaks
  maxGapMs: number; // maximum time between the two peaks
}

export const DEFAULT_CLAP_PARAMS: ClapDetectorParams = {
  threshold: 0.15,
  minGapMs: 100,
  maxGapMs: 800,
};

export class DoubleClapDetector {
  private readonly params: ClapDetectorParams;
  private aboveThreshold = false;
  private firstPeakAt: number | null = null;

  constructor(params: ClapDetectorParams = DEFAULT_CLAP_PARAMS) {
    this.params = params;
  }

  /**
   * Feed one RMS reading at timestamp `tsMs` (milliseconds, monotonic).
   * Returns `true` exactly once per detected double-clap.
   */
  feed(rms: number, tsMs: number): boolean {
    const wasAbove = this.aboveThreshold;
    const nowAbove = rms > this.params.threshold;
    this.aboveThreshold = nowAbove;

    // Expire a stale "awaiting second peak" window.
    if (
      this.firstPeakAt !== null &&
      tsMs - this.firstPeakAt > this.params.maxGapMs
    ) {
      this.firstPeakAt = null;
    }

    const isRisingEdge = !wasAbove && nowAbove;
    if (!isRisingEdge) return false;

    if (this.firstPeakAt === null) {
      // First clap: arm the window.
      this.firstPeakAt = tsMs;
      return false;
    }

    const gap = tsMs - this.firstPeakAt;
    if (gap >= this.params.minGapMs && gap <= this.params.maxGapMs) {
      // Double-clap detected. Reset for the next one.
      this.firstPeakAt = null;
      return true;
    }

    if (gap > this.params.maxGapMs) {
      // Window expired between feeds; treat this edge as a new "first peak".
      this.firstPeakAt = tsMs;
    }
    // gap < minGapMs: too soon — silently ignore (refractory).
    return false;
  }

  reset(): void {
    this.aboveThreshold = false;
    this.firstPeakAt = null;
  }
}
