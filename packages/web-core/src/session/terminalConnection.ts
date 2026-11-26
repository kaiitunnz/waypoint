import { connectTerminalSocket } from "../api";
import { reconnectDelay } from "./connection";
import { terminalHello, type TerminalAppearance, type TerminalDims } from "./terminal";

export type TerminalConnectionState = "connecting" | "open" | "reconnecting" | "exited";

// The pane the stream paints into.
export interface TerminalSink {
  reset(): void;
  write(data: string): void;
  setAppearance(appearance: TerminalAppearance): void;
  // A fixed-grid pane announces its server-side geometry.
  resize(cols: number, rows: number): void;
  // The grid as it is now, for a resizable pane's hello.
  dims(): TerminalDims | null;
}

export interface TerminalConnectionOptions {
  host: string;
  token: string;
  sessionId: string;
  resizable: boolean;
  // Asks a locked-by-default pane to accept typing.
  interactive: boolean;
  // Read on every use: the pane may mount after the connection opens.
  sink: () => TerminalSink | null;
  onState?: (state: TerminalConnectionState) => void;
  onAppearance?: (appearance: TerminalAppearance) => void;
  onAuthFailure?: () => void;
}

export interface TerminalConnection {
  // Each returns false when the socket is not open.
  input(data: string): boolean;
  // One whole message; the server presses Enter after it.
  submit(text: string): boolean;
  resize(cols: number, rows: number): boolean;
  close(): void;
}

// Streams a session's terminal pane, reconnecting with backoff on a dropped
// socket. It stops for good when the session's pane is gone or auth fails.
export function openTerminalConnection(options: TerminalConnectionOptions): TerminalConnection {
  const { host, token, sessionId, resizable, interactive, sink, onState, onAppearance, onAuthFailure } = options;
  let active = true;
  let socket: WebSocket | null = null;
  let timer: ReturnType<typeof setTimeout> | null = null;
  let attempt = 0;

  function connect(state: "connecting" | "reconnecting") {
    onState?.(state);
    const pane = sink();
    pane?.reset();
    // Every connection starts dark so a prior light pane cannot bleed into
    // this one; the server sends the appearance frame when it is light.
    pane?.setAppearance("dark");
    onAppearance?.("dark");
    const current = connectTerminalSocket(host, token, sessionId, {
      onOpen: () => {
        if (current.readyState !== WebSocket.OPEN) return;
        attempt = 0;
        onState?.("open");
        const dims = resizable ? (sink()?.dims() ?? null) : null;
        current.send(JSON.stringify(terminalHello(dims, interactive)));
      },
      onChunk: (text) => sink()?.write(text),
      onAppearance: (appearance) => {
        // Applied before the seed is written, so it paints on the right ground.
        sink()?.setAppearance(appearance);
        onAppearance?.(appearance);
      },
      onResize: (cols, rows) => sink()?.resize(cols, rows),
      onAuthFailure: () => {
        active = false;
        onAuthFailure?.();
      },
      onSessionExited: () => {
        active = false;
        onState?.("exited");
      },
      onClose: () => {
        if (!active) return;
        const delay = reconnectDelay(attempt);
        attempt += 1;
        onState?.("reconnecting");
        timer = setTimeout(() => {
          if (active) connect("reconnecting");
        }, delay);
      },
    });
    socket = current;
  }

  function send(message: object): boolean {
    if (socket?.readyState !== WebSocket.OPEN) return false;
    socket.send(JSON.stringify(message));
    return true;
  }

  connect("connecting");
  return {
    input: (data) => send({ type: "input", data }),
    submit: (text) => send({ type: "input_submit", text, submit: true }),
    resize: (cols, rows) => (resizable ? send({ type: "resize", cols, rows }) : false),
    close: () => {
      active = false;
      if (timer) clearTimeout(timer);
      socket?.close();
      socket = null;
    },
  };
}
