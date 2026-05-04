/**
 * Shared, non-reactive bus for AnalyserNodes that the Three.js scene reads
 * each animation frame. Producers (AudioCapture, VoicePipeline) call
 * `setMicAnalyser` / `setPlaybackAnalyser`; the scene calls the getters
 * inside its `requestAnimationFrame` loop.
 *
 * Kept outside the Zustand store so updating the analyser doesn't trigger
 * React re-renders — analysers change ownership but the value isn't UI
 * state.
 */

class AudioBus {
  private mic: AnalyserNode | null = null;
  private playback: AnalyserNode | null = null;

  setMicAnalyser(node: AnalyserNode | null): void {
    this.mic = node;
  }

  getMicAnalyser(): AnalyserNode | null {
    return this.mic;
  }

  setPlaybackAnalyser(node: AnalyserNode | null): void {
    this.playback = node;
  }

  getPlaybackAnalyser(): AnalyserNode | null {
    return this.playback;
  }
}

export const audioBus = new AudioBus();
