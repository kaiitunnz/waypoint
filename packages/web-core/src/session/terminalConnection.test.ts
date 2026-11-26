import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { openTerminalConnection, type TerminalSink } from "./terminalConnection";

class FakeSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSED = 3;
  static instances: FakeSocket[] = [];
  readyState = FakeSocket.CONNECTING;
  sent: unknown[] = [];
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: unknown }) => void) | null = null;
  onclose: ((event: { code: number }) => void) | null = null;

  constructor(readonly url: string) {
    FakeSocket.instances.push(this);
  }

  send(data: string) {
    this.sent.push(JSON.parse(data));
  }

  close() {
    this.drop(1000);
  }

  open() {
    this.readyState = FakeSocket.OPEN;
    this.onopen?.();
  }

  receive(data: string) {
    this.onmessage?.({ data });
  }

  drop(code: number) {
    if (this.readyState === FakeSocket.CLOSED) return;
    this.readyState = FakeSocket.CLOSED;
    this.onclose?.({ code });
  }
}

const latest = () => FakeSocket.instances[FakeSocket.instances.length - 1];

function fakeSink(dims = { cols: 100, rows: 30 }) {
  const calls: string[] = [];
  const sink: TerminalSink = {
    reset: () => calls.push("reset"),
    write: (data) => calls.push(`write:${data}`),
    setAppearance: (appearance) => calls.push(`appearance:${appearance}`),
    resize: (cols, rows) => calls.push(`resize:${cols}x${rows}`),
    dims: () => dims,
  };
  return { sink, calls };
}

function open(patch: Partial<Parameters<typeof openTerminalConnection>[0]> = {}) {
  const { sink, calls } = fakeSink();
  const states: string[] = [];
  const appearances: string[] = [];
  const onAuthFailure = vi.fn();
  const connection = openTerminalConnection({
    host: "http://h:1",
    token: "t k",
    sessionId: "s1",
    resizable: true,
    interactive: false,
    sink: () => sink,
    onState: (state) => states.push(state),
    onAppearance: (appearance) => appearances.push(appearance),
    onAuthFailure,
    ...patch,
  });
  return { connection, calls, states, appearances, onAuthFailure };
}

beforeEach(() => {
  FakeSocket.instances = [];
  vi.useFakeTimers();
  vi.stubGlobal("WebSocket", FakeSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("openTerminalConnection", () => {
  it("connects to the session's terminal socket on a dark, reset pane", () => {
    const { calls, states, appearances } = open();
    expect(latest().url).toBe("ws://h:1/ws/sessions/s1/terminal?token=t%20k");
    expect(calls).toEqual(["reset", "appearance:dark"]);
    expect(states).toEqual(["connecting"]);
    expect(appearances).toEqual(["dark"]);
  });

  it("says hello with the grid of a resizable pane", () => {
    const { states } = open();
    latest().open();
    expect(latest().sent).toEqual([{ type: "hello", terminal_protocol: 2, cols: 100, rows: 30 }]);
    expect(states).toEqual(["connecting", "open"]);
  });

  it("says hello without a grid for a fixed pane, interactive when unlocked", () => {
    open({ resizable: false, interactive: true });
    latest().open();
    expect(latest().sent).toEqual([{ type: "hello", terminal_protocol: 2, interactive: true }]);
  });

  it("paints output, applies appearance and the server's grid", () => {
    const { calls, appearances } = open({ resizable: false });
    latest().open();
    latest().receive('{"type":"appearance","appearance":"light"}');
    latest().receive('{"type":"size","cols":120,"rows":34}');
    latest().receive("\x1b[?2026hhi");
    expect(calls.slice(2)).toEqual(["appearance:light", "resize:120x34", "write:\x1b[?2026hhi"]);
    expect(appearances).toEqual(["dark", "light"]);
  });

  it("sends input, whole messages and resizes only while open", () => {
    const { connection } = open();
    expect(connection.input("a")).toBe(false);
    latest().open();
    expect(connection.input("a")).toBe(true);
    expect(connection.submit("ls -la")).toBe(true);
    expect(connection.resize(90, 20)).toBe(true);
    expect(latest().sent.slice(1)).toEqual([
      { type: "input", data: "a" },
      { type: "input_submit", text: "ls -la", submit: true },
      { type: "resize", cols: 90, rows: 20 },
    ]);
  });

  it("never sends resize frames for a fixed pane", () => {
    const { connection } = open({ resizable: false });
    latest().open();
    expect(connection.resize(90, 20)).toBe(false);
    expect(latest().sent).toHaveLength(1);
  });

  it("reconnects with backoff after a drop and resets the backoff once open", () => {
    const { states, calls } = open();
    latest().open();
    latest().drop(1006);
    expect(states.at(-1)).toBe("reconnecting");
    vi.advanceTimersByTime(999);
    expect(FakeSocket.instances).toHaveLength(1);
    vi.advanceTimersByTime(1);
    expect(FakeSocket.instances).toHaveLength(2);
    expect(calls.filter((call) => call === "reset")).toHaveLength(2);
    latest().drop(1006);
    vi.advanceTimersByTime(2000);
    expect(FakeSocket.instances).toHaveLength(3);
    latest().open();
    latest().drop(1006);
    vi.advanceTimersByTime(1000);
    expect(FakeSocket.instances).toHaveLength(4);
  });

  it("stops when the session's pane has exited", () => {
    const { states } = open();
    latest().open();
    latest().drop(4410);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(states.at(-1)).toBe("exited");
  });

  it("stops and reports an auth failure", () => {
    const { onAuthFailure } = open();
    latest().drop(4401);
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
    expect(onAuthFailure).toHaveBeenCalledOnce();
  });

  it("reports nothing once closed", () => {
    const { connection, states, onAuthFailure } = open();
    latest().open();
    const socket = latest();
    connection.close();
    socket.onclose?.({ code: 4410 });
    socket.onclose?.({ code: 4401 });
    expect(states.at(-1)).toBe("open");
    expect(onAuthFailure).not.toHaveBeenCalled();
  });

  it("closes without reconnecting, also while a retry is pending", () => {
    const first = open();
    first.connection.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);

    FakeSocket.instances = [];
    const second = open();
    latest().drop(1006);
    second.connection.close();
    vi.advanceTimersByTime(60_000);
    expect(FakeSocket.instances).toHaveLength(1);
  });
});
