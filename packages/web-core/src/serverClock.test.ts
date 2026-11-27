import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectInboxSocket, connectSessionSocket, connectSessionsSocket } from "./api";
import { inboxRelativeTime } from "./inbox";
import { formatRelative } from "./scheduleTime";
import {
  estimateClockOffset,
  serverClockOffset,
  serverNow,
  subscribeServerClock,
  syncServerClock,
} from "./serverClock";
import { schedulePreview } from "./session/scheduleMessage";
import { schedulePollIntervalMs } from "./session/scheduledMessages";
import { formatElapsed } from "./session/workingRow";
import { formatSessionTime } from "./sessionPresentation";
import { formatRelativeTime } from "./usage";
import { formatResetsIn } from "./usageSummary";
import type { MessageSchedule } from "./types";

// The server's clock, mid-second so the header's truncation shows.
const SERVER = Date.parse("2026-11-27T12:00:00.400Z");
const MINUTES_3 = 180_000;

const header = (at: number) => new Date(at).toUTCString();

// A server that answers with its clock at `server`, or with no readable `Date`.
function serve(server: number | null) {
  const fetch = vi.fn(async () => ({
    headers: { get: () => (server === null ? null : header(server)) },
  }));
  vi.stubGlobal("fetch", fetch);
  return fetch;
}

let hosts = 0;
const freshHost = () => `http://host-${(hosts += 1)}`;

beforeEach(() => vi.useFakeTimers());
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("estimateClockOffset", () => {
  const sent = SERVER;
  const received = SERVER + 100;

  it("reads agreeing clocks as no offset, whatever the header's truncation", () => {
    expect(estimateClockOffset(header(SERVER), sent, received)).toBe(0);
    expect(estimateClockOffset(header(SERVER - 1000), sent, received)).toBe(0);
  });

  it.each([
    ["fast", MINUTES_3],
    ["slow", -MINUTES_3],
  ])("measures a client three minutes %s", (_, skew) => {
    const offset = estimateClockOffset(header(SERVER), sent + skew, received + skew)!;
    expect(Math.abs(offset + skew)).toBeLessThanOrEqual(2000);
    // Erring late: the corrected clock never trails the server's.
    expect(received + skew + offset).toBeGreaterThanOrEqual(received);
  });

  it("has no answer without a readable date", () => {
    expect(estimateClockOffset(null, sent, received)).toBeNull();
    expect(estimateClockOffset("soon", sent, received)).toBeNull();
  });
});

describe("syncServerClock", () => {
  it("corrects serverNow and tells subscribers once", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    serve(SERVER);
    const listener = vi.fn();
    const stop = subscribeServerClock(listener);
    const host = freshHost();
    await syncServerClock(host);
    expect(Math.abs(serverNow() - SERVER)).toBeLessThanOrEqual(2000);
    expect(listener).toHaveBeenCalledTimes(1);
    await syncServerClock(host);
    expect(listener).toHaveBeenCalledTimes(1);
    stop();
  });

  it("asks once while a measurement is running", async () => {
    vi.setSystemTime(SERVER);
    const fetch = serve(SERVER);
    const host = freshHost();
    await Promise.all([syncServerClock(host), syncServerClock(host)]);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("starts a new host from no offset, even one it cannot reach", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    serve(SERVER);
    await syncServerClock(freshHost());
    expect(serverClockOffset()).not.toBe(0);
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await syncServerClock(freshHost());
    expect(serverClockOffset()).toBe(0);
  });

  it("takes a server that exposes no date as agreeing", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    const host = freshHost();
    serve(SERVER);
    await syncServerClock(host);
    serve(null);
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(0);
  });

  it("keeps the offset when an answer is too slow to read the clocks by", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    serve(SERVER);
    const host = freshHost();
    await syncServerClock(host);
    const held = serverClockOffset();
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => {
        vi.setSystemTime(Date.now() + 10 * 60_000);
        return { headers: { get: () => header(SERVER) } };
      }),
    );
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(held);
  });

  it("keeps the offset when the same host cannot be reached", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    serve(SERVER);
    const host = freshHost();
    await syncServerClock(host);
    const held = serverClockOffset();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("offline")));
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(held);
  });

  it("drops an answer from a host that is no longer the one connected", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    let answer: (response: unknown) => void = () => {};
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => (answer = resolve))));
    const slow = syncServerClock(freshHost());
    const late = answer;
    serve(null);
    await syncServerClock(freshHost());
    late({ headers: { get: () => header(SERVER) } });
    await slow;
    expect(serverClockOffset()).toBe(0);
  });
});

describe("a connection", () => {
  class FakeSocket {
    static opened: FakeSocket[] = [];
    onopen: (() => void) | null = null;
    onmessage: unknown = null;
    onclose: unknown = null;
    constructor() {
      FakeSocket.opened.push(this);
    }
  }

  it.each([
    ["sessions", (host: string) => connectSessionsSocket(host, "t", () => {})],
    ["session", (host: string) => connectSessionSocket(host, "t", "s1", () => {})],
    ["inbox", (host: string) => connectInboxSocket(host, "t", "i1", () => {})],
  ])("measures the clock when the %s socket opens", (_, connect) => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const fetch = serve(SERVER);
    const host = freshHost();
    connect(host);
    expect(fetch).not.toHaveBeenCalled();
    FakeSocket.opened.at(-1)?.onopen?.();
    expect(fetch).toHaveBeenCalledWith(`${host}/health`, expect.objectContaining({ cache: "no-store" }));
  });
});

describe.each([
  ["fast", MINUTES_3],
  ["slow", -MINUTES_3],
])("labels on a client three minutes %s", (_, skew) => {
  beforeEach(async () => {
    vi.setSystemTime(SERVER + skew);
    serve(SERVER);
    await syncServerClock(freshHost());
  });

  it("reads a time the server has just written as just now", () => {
    const written = new Date(SERVER).toISOString();
    expect(["now", "1 second ago", "2 seconds ago"]).toContain(formatRelativeTime(written));
    expect(formatSessionTime(written)).toBe("now");
  });

  it("times a turn from the server's clock", () => {
    const started = SERVER - 42_000;
    const seconds = Number(formatElapsed(serverNow() - started).replace("s", ""));
    expect(seconds).toBeGreaterThanOrEqual(42);
    expect(seconds).toBeLessThanOrEqual(44);
  });

  it("counts a reset and a schedule down from the server's clock", () => {
    expect(formatResetsIn(new Date(SERVER + 3_600_000).toISOString())).toBe("1h");
    expect(formatRelative(new Date(SERVER + 600_000))).toBe("in 10m");
  });

  it("ages an inbox item from the server's clock", () => {
    expect(inboxRelativeTime(new Date(SERVER - 600_000).toISOString())).toBe(
      inboxRelativeTime(new Date(SERVER - 600_000).toISOString(), SERVER),
    );
  });

  it("polls a held message by when the server will send it", () => {
    const due = (ahead: number) => [{ scheduled_at: new Date(SERVER + ahead).toISOString() } as MessageSchedule];
    expect(schedulePollIntervalMs(due(10_000))).toBe(4_000);
    expect(schedulePollIntervalMs(due(120_000))).toBe(15_000);
  });

  it("previews a picked time by the server's clock and a delay by itself", () => {
    // The picked time is ten minutes ahead of the server's clock.
    const picked = new Date(SERVER + 600_000);
    const local = new Date(picked.getTime() - picked.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
    expect(schedulePreview("datetime", "", local, new Date())?.relative).toBe("in 10 min");
    expect(schedulePreview("delay", "15", "", new Date())?.relative).toBe("in 15 min");
  });
});
