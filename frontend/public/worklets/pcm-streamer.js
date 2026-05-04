/**
 * AudioWorklet processor that streams Float32 mono frames to the main
 * thread for the JARVIS voice pipeline.
 *
 * Each `process()` call gets ~128 samples (the Web Audio quantum). We
 * accumulate `windowSize` samples then post them as a single batch — a
 * good balance between latency (<25 ms at 16 kHz) and overhead.
 *
 * The worklet emits BOTH the raw frame and its RMS so the main thread
 * can drive client-side VAD without re-walking the buffer.
 */
class PcmStreamerProcessor extends AudioWorkletProcessor {
  constructor(options) {
    super();
    const opts = (options && options.processorOptions) || {};
    this.windowSize = opts.windowSize || 320; // 20 ms @ 16 kHz, 6.7 ms @ 48 kHz
    this.buffer = new Float32Array(this.windowSize);
    this.idx = 0;
    this.active = true;

    this.port.onmessage = (event) => {
      const data = event.data;
      if (data && data.type === "stop") {
        this.active = false;
      } else if (data && data.type === "start") {
        this.active = true;
        this.idx = 0;
      }
    };
  }

  process(inputs) {
    if (!this.active) return true;
    const input = inputs[0];
    if (!input || input.length === 0) return true;
    const channel = input[0];
    if (!channel || channel.length === 0) return true;

    for (let i = 0; i < channel.length; i++) {
      this.buffer[this.idx++] = channel[i];
      if (this.idx >= this.windowSize) {
        // Compute RMS so the main thread can VAD without re-walking.
        let sumSq = 0;
        for (let j = 0; j < this.windowSize; j++) {
          const s = this.buffer[j];
          sumSq += s * s;
        }
        const rms = Math.sqrt(sumSq / this.windowSize);

        // Transferable copy so we don't mutate the live buffer while the
        // main thread reads it.
        const out = new Float32Array(this.buffer);
        this.port.postMessage({ pcm: out, rms, t: currentTime }, [out.buffer]);
        this.idx = 0;
      }
    }
    return true;
  }
}

registerProcessor("pcm-streamer", PcmStreamerProcessor);
