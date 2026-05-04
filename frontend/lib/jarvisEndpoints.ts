/**
 * Backend URL helpers — keep WebSocket and REST on the same host/port by default.
 *
 * Set `NEXT_PUBLIC_JARVIS_WS_URL` (e.g. `ws://localhost:8000/ws`) and HTTP base is
 * derived unless you override with `NEXT_PUBLIC_JARVIS_HTTP_URL`.
 */

const DEFAULT_WS = "ws://localhost:8000/ws";

function wsUrlToHttpBase(wsUrl: string): string {
  try {
    const u = new URL(wsUrl);
    const scheme = u.protocol === "wss:" ? "https:" : "http:";
    return `${scheme}//${u.host}`;
  } catch {
    return "http://localhost:8000";
  }
}

export function getJarvisWsUrl(): string {
  const raw = process.env.NEXT_PUBLIC_JARVIS_WS_URL?.trim();
  return raw || DEFAULT_WS;
}

/** REST API origin (no trailing slash). */
export function getJarvisHttpBase(): string {
  const explicit = process.env.NEXT_PUBLIC_JARVIS_HTTP_URL?.trim();
  if (explicit) return explicit.replace(/\/$/, "");
  return wsUrlToHttpBase(getJarvisWsUrl());
}

const PROFILE_FETCH_MS = 4000;

/**
 * Returns ``true`` if the setup wizard should show, ``false`` if not, ``null`` if
 * the backend could not be reached (offline / wrong port). Fails fast so the
 * browser does not hang on a long timeout.
 */
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
