/**
 * Backend URL helpers.
 *
 * Priority order:
 *  1. localStorage["jarvis_backend_url"]  — set via in-app settings (mobile / Tailscale)
 *  2. NEXT_PUBLIC_JARVIS_WS_URL env var   — baked in at build time (Tauri desktop)
 *  3. Same host as the page              — default for browser dev
 */

const STORAGE_KEY = "jarvis_backend_url";
const DEFAULT_WS = "ws://localhost:8000/ws";

function storedBackendUrl(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return localStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Save a new backend base URL (e.g. "http://100.x.x.x:8000") to localStorage. */
export function saveBackendUrl(httpBase: string): void {
  if (typeof window === "undefined") return;
  const clean = httpBase.replace(/\/$/, "");
  localStorage.setItem(STORAGE_KEY, clean);
}

export function clearBackendUrl(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(STORAGE_KEY);
}

function wsUrlToHttpBase(wsUrl: string): string {
  try {
    const u = new URL(wsUrl);
    const scheme = u.protocol === "wss:" ? "https:" : "http:";
    return `${scheme}//${u.host}`;
  } catch {
    return "http://localhost:8000";
  }
}

function httpBaseToWs(httpBase: string): string {
  try {
    const u = new URL(httpBase);
    const scheme = u.protocol === "https:" ? "wss:" : "ws:";
    return `${scheme}//${u.host}/ws`;
  } catch {
    return DEFAULT_WS;
  }
}

export function getJarvisWsUrl(): string {
  const stored = storedBackendUrl();
  if (stored) return httpBaseToWs(stored);
  const env = process.env.NEXT_PUBLIC_JARVIS_WS_URL?.trim();
  return env || DEFAULT_WS;
}

export function getJarvisHttpBase(): string {
  const stored = storedBackendUrl();
  if (stored) return stored;
  const explicit = process.env.NEXT_PUBLIC_JARVIS_HTTP_URL?.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  const envWs = process.env.NEXT_PUBLIC_JARVIS_WS_URL?.trim();
  if (envWs) return wsUrlToHttpBase(envWs);
  return "http://localhost:8000";
}

const PROFILE_FETCH_MS = 4000;

export async function fetchProfileNeedsSetup(): Promise<boolean | null> {
  const base = getJarvisHttpBase();
  const ac = new AbortController();
  const id = setTimeout(() => ac.abort(), PROFILE_FETCH_MS);
  try {
    const r = await fetch(`${base}/profile/status`, { signal: ac.signal });
    if (!r.ok) return null;
    const j = (await r.json()) as { needs_setup?: boolean };
    return j.needs_setup === true;
  } catch {
    const debug =
      typeof window !== "undefined" &&
      new URLSearchParams(window.location.search).get("debug") === "1";
    if (debug) {
      console.warn(
        "[jarvis] GET /profile/status failed — start the FastAPI backend on",
        base,
      );
    }
    return null;
  } finally {
    clearTimeout(id);
  }
}
