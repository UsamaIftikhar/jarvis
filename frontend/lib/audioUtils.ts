/**
 * Web Audio API helpers shared by clap-detection (Phase 2) and the mic
 * pipeline (Phase 3).
 */

export interface MicSource {
  context: AudioContext;
  source: MediaStreamAudioSourceNode;
  stream: MediaStream;
  release: () => Promise<void>;
}

export interface OpenMicOptions {
  /** Hint the AudioContext sample rate. The OS may override on macOS/Linux. */
  sampleRate?: number;
  /**
   * For clap detection we want raw transients — turn OFF noise suppression,
   * AGC, and echo cancellation. For STT (Phase 3) we leave them ON.
   */
  rawTransients?: boolean;
}

/**
 * Request the microphone and return an AudioContext + source node.
 * Caller is responsible for `release()`-ing when done.
 *
 * Must be called from a user-gesture event handler (click / keydown);
 * browsers refuse to grant `getUserMedia` from passive contexts.
 */
export async function openMic(
  options: OpenMicOptions = {},
): Promise<MicSource> {
  const { sampleRate = 16000, rawTransients = false } = options;

  const stream = await navigator.mediaDevices.getUserMedia({
    audio: {
      channelCount: 1,
      echoCancellation: !rawTransients,
      noiseSuppression: !rawTransients,
      autoGainControl: false,
    },
    video: false,
  });

  // Most browsers ignore the sampleRate hint. We resample later if needed
  // (Phase 3) before sending to faster-whisper.
  const context = new AudioContext({ sampleRate });

  // Browsers create AudioContexts in "suspended" state when they aren't
  // synchronously inside a user-gesture frame. `getUserMedia` is async, so
  // by the time we land here the gesture is gone — explicitly resume()
  // or the AudioWorklet's process() will never fire and no PCM/RMS frames
  // will ever reach the main thread.
  if (context.state === "suspended") {
    try {
      await context.resume();
    } catch {
      /* If resume fails the caller will notice via silent worklet output;
         we don't throw here so the rest of the pipeline can still wire up. */
    }
  }

  const source = context.createMediaStreamSource(stream);

  return {
    context,
    source,
    stream,
    release: async () => {
      stream.getTracks().forEach((t) => t.stop());
      if (context.state !== "closed") {
        try {
          await context.close();
        } catch {
          /* already closed */
        }
      }
    },
  };
}

/** Compute root-mean-square of a Float32 PCM frame. (Used by tests.) */
export function rms(frame: Float32Array): number {
  let sumSq = 0;
  for (let i = 0; i < frame.length; i++) {
    const s = frame[i] ?? 0;
    sumSq += s * s;
  }
  return Math.sqrt(sumSq / frame.length);
}
