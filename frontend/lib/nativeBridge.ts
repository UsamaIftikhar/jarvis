/**
 * Bridge to native iOS capabilities via Capacitor.
 * Falls back to no-ops on web so the same code runs everywhere.
 */

type WakeEventListener = () => void;

interface JarvisWakePlugin {
  startListening(options: { phrases: string[] }): Promise<void>;
  stopListening(): Promise<void>;
  addListener(
    event: "wakeWord",
    handler: (data: { phrase: string }) => void,
  ): Promise<{ remove: () => void }>;
}

function isCapacitorNative(): boolean {
  return (
    typeof window !== "undefined" &&
    // @ts-expect-error Capacitor injects this at runtime
    typeof window.Capacitor !== "undefined" &&
    // @ts-expect-error
    window.Capacitor.isNativePlatform()
  );
}

function getPlugin(): JarvisWakePlugin | null {
  if (!isCapacitorNative()) return null;
  // @ts-expect-error
  return window.Capacitor?.Plugins?.JarvisWakePlugin ?? null;
}

let _removeListener: (() => void) | null = null;

export async function startNativeWakeWord(
  onWake: WakeEventListener,
): Promise<boolean> {
  const plugin = getPlugin();
  if (!plugin) return false;

  try {
    const handle = await plugin.addListener("wakeWord", () => onWake());
    _removeListener = handle.remove;
    await plugin.startListening({
      phrases: [
        "hey jarvis",
        "hey javis",
        "wake up jarvis",
        "jarvis",
        "hey travis",
      ],
    });
    console.log("[nativeBridge] native wake word started");
    return true;
  } catch (e) {
    console.warn("[nativeBridge] startNativeWakeWord failed:", e);
    return false;
  }
}

export async function stopNativeWakeWord(): Promise<void> {
  const plugin = getPlugin();
  _removeListener?.();
  _removeListener = null;
  try {
    await plugin?.stopListening();
  } catch {
    /* already stopped */
  }
}

export function isNativePlatform(): boolean {
  return isCapacitorNative();
}
