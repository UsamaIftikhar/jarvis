/**
 * Typed wrapper around the browser WebSocket for the JARVIS backend.
 *
 * Phase 1: JSON control frames + auto-reconnect.
 * Phase 3: adds binary frames in both directions
 *   - client -> server: Int16 LE PCM (mic audio)
 *   - server -> client: Int16 LE PCM (TTS audio)
 *
 * The send/receive protocol is documented at the top of `backend/main.py`.
 */

export type ServerMessage =
  | {
      type: "hello";
      server: string;
      version: string;
      needs_profile_setup?: boolean;
    }
  | { type: "pong"; ts: number }
  | { type: "echo"; text: string }
  // ---- Phase 3 turn lifecycle ----
  | { type: "thinking" }
  | { type: "thinking_step"; label: string }
  | { type: "transcript"; text: string }
  | { type: "token"; text: string }
  | { type: "final"; text: string }
  | { type: "speaking_start"; sample_rate: number }
  | { type: "speaking_end" }
  | {
      type: "proactive_alert";
      message: string;
      priority: "low" | "medium" | "high";
      speak?: boolean;
    }
  | {
      type: "reminder";
      message: string;
      name?: string;
      speak?: boolean;
    }
  // ---- Errors ----
  | { type: "error"; message: string };

export type ClientMessage =
  | { type: "ping"; ts: number }
  | { type: "echo"; text: string }
  | { type: "utterance_start"; sample_rate: number }
  | { type: "utterance_end" }
  | { type: "cancel" }
  | { type: "text_input"; text: string };

export interface WsClientHandlers {
  onOpen?: () => void;
  onClose?: () => void;
  onError?: (err: Event) => void;
  onMessage?: (msg: ServerMessage) => void;
  /** Called for every binary frame from the server (TTS PCM in Phase 3). */
  onBinary?: (data: ArrayBuffer) => void;
}

export class JarvisWsClient {
  private ws: WebSocket | null = null;
  private url: string;
  private handlers: WsClientHandlers;
  private shouldReconnect = true;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(url: string, handlers: WsClientHandlers = {}) {
    this.url = url;
    this.handlers = handlers;
  }

  connect(): void {
    if (this.ws && this.ws.readyState <= WebSocket.OPEN) return;
    this.shouldReconnect = true;

    const ws = new WebSocket(this.url);
    ws.binaryType = "arraybuffer";
    this.ws = ws;

    ws.addEventListener("open", () => {
      this.reconnectAttempts = 0;
      this.handlers.onOpen?.();
    });

    ws.addEventListener("close", () => {
      this.handlers.onClose?.();
      if (this.shouldReconnect) this.scheduleReconnect();
    });

    ws.addEventListener("error", (err) => {
      this.handlers.onError?.(err);
    });

    ws.addEventListener("message", (event) => {
      if (typeof event.data === "string") {
        try {
          const parsed = JSON.parse(event.data) as ServerMessage;
          this.handlers.onMessage?.(parsed);
        } catch {
          /* malformed; ignore */
        }
        return;
      }
      // Binary frame.
      if (event.data instanceof ArrayBuffer) {
        this.handlers.onBinary?.(event.data);
      } else if (event.data instanceof Blob) {
        // Defensive — we set binaryType=arraybuffer but some browsers
        // can still hand us a Blob in edge cases.
        event.data.arrayBuffer().then((ab) => this.handlers.onBinary?.(ab));
      }
    });
  }

  send(msg: ClientMessage): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(JSON.stringify(msg));
  }

  /** Send a binary frame (used for streaming mic PCM in Phase 3). */
  sendBinary(buf: ArrayBuffer | ArrayBufferView): void {
    if (this.ws?.readyState !== WebSocket.OPEN) return;
    this.ws.send(buf);
  }

  isOpen(): boolean {
    return this.ws?.readyState === WebSocket.OPEN;
  }

  close(): void {
    this.shouldReconnect = false;
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    const ws = this.ws;
    this.ws = null;
    // Critical: drop our handlers BEFORE the socket fires any of its
    // remaining events. In React StrictMode the lifecycle is
    //   mount -> connect WS1 -> cleanup -> close WS1 -> remount -> connect WS2
    // and WS1's pending open/close events can race with WS2's. Without
    // this line, WS1's late close() callback would fire `onClose` on the
    // shared store and flip the link chip to "disconnected" even though
    // WS2 is alive and well.
    this.handlers = {};
    if (!ws) return;
    if (ws.readyState === WebSocket.CONNECTING) {
      // Calling close() while CONNECTING also fires a noisy
      // "WebSocket is closed before the connection is established"
      // warning in devtools. Defer the close until the socket either
      // opens (and we close cleanly) or errors out on its own.
      const finish = () => {
        try {
          ws.close();
        } catch {
          /* already closed */
        }
      };
      ws.addEventListener("open", finish, { once: true });
      ws.addEventListener("error", finish, { once: true });
      return;
    }
    if (ws.readyState !== WebSocket.CLOSED) {
      ws.close();
    }
  }

  private scheduleReconnect(): void {
    const delay = Math.min(5000, 250 * 2 ** this.reconnectAttempts);
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => this.connect(), delay);
  }
}
