/**
 * AudioWorklet processor for JARVIS clap detection.
 *
 * Runs on the audio thread (so it never gets blocked by main-thread jank).
 * Accumulates incoming PCM into a fixed-size window, computes the RMS of
 * that window, and posts the value back to the main thread roughly every
 * `windowSize / sampleRate` seconds.
 *
 * The peak-detection logic itself lives on the main thread in
 * `lib/clapDetector.ts`. This worklet only emits raw RMS samples so the
 * detector logic stays unit-testable in plain JS.
 */
class ClapDetectorProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    // 1024 samples @ 48 kHz ≈ 21 ms per RMS reading. Plenty of resolution
    // for the spec's 100–800 ms double-clap window.
    this.windowSize = opts.windowSize || 1024;
    this.buffer = new Float32Array(this.windowSize);
    this.idx = 0;
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.idx++] = channel[i];
      if (this.idx >= this.windowSize) {
        // Compute RMS of the completed window.
        let sumSq = 0;
        for (let j = 0; j < this.windowSize; j++) {
          const s = this.buffer[j];
          sumSq += s * s;
        }
        const rms = Math.sqrt(sumSq / this.windowSize);
        // currentTime is provided by the AudioWorkletGlobalScope (seconds).
        this.port.postMessage({ rms, t: currentTime });
        this.idx = 0;
      }
    }
    return true;
  }
}

registerProcessor("clap-detector", ClapDetectorProcessor);
