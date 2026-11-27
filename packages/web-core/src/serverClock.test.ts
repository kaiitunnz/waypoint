import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { connectInboxSocket, connectSessionSocket, connectSessionsSocket } from "./api";
import { formatInboxTime, inboxRelativeTime, updatedToday } from "./inbox";
import { formatThreadTime } from "./launch/threads";
import { formatClock, formatRelative } from "./scheduleTime";
import { serverClockOffset, serverNow, subscribeServerClock, syncServerClock } from "./serverClock";
import { relativeFromNow, schedulePreview } from "./session/scheduleMessage";
import { schedulePollIntervalMs } from "./session/scheduledMessages";
import { formatElapsed } from "./session/workingRow";
import { formatSessionTime } from "./sessionPresentation";
import { formatRelativeTime } from "./usage";
import {
  accountWindows,
  formatResetsIn,
  formatShortWhen,
  mostConstrainedWindow,
  summarizeScheduled,
} from "./usageSummary";
import type { InboxItem, MessageSchedule, ScheduledSession, UsageDashboardBucket } from "./types";

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

describe("syncServerClock", () => {
  it("reads agreeing clocks as no offset, whatever the header's truncation", async () => {
    vi.setSystemTime(SERVER);
    for (const at of [SERVER, SERVER - 1000]) {
      serve(at);
      await syncServerClock(freshHost());
      expect(serverClockOffset()).toBe(0);
    }
  });

  it.each([
    ["fast", MINUTES_3],
    ["slow", -MINUTES_3],
  ])("measures a client three minutes %s", async (_, skew) => {
    vi.setSystemTime(SERVER + skew);
    serve(SERVER);
    await syncServerClock(freshHost());
    expect(Math.abs(serverClockOffset() + skew)).toBeLessThanOrEqual(2000);
    // Erring late: the corrected clock never trails the server's.
    expect(serverNow()).toBeGreaterThanOrEqual(SERVER);
  });

  it("holds the offset when a later header says less", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    const host = freshHost();
    serve(SERVER - 1000);
    await syncServerClock(host);
    const held = serverClockOffset();
    serve(SERVER);
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(held);
  });

  it("narrows the offset by every header from the host", async () => {
    // The server is a second ahead.
    vi.setSystemTime(SERVER - 1000);
    const host = freshHost();
    const read = async (at: number | null) => {
      serve(at);
      await syncServerClock(host);
      return serverClockOffset();
    };
    expect(await read(SERVER - 1000)).toBe(0);
    // An answer without a date leaves what is known.
    expect(await read(null)).toBe(0);
    expect(await read(SERVER)).toBe(1600);
    expect(await read(SERVER - 1000)).toBe(1600);
  });

  it("reads a new host's headers without the last host's", async () => {
    vi.setSystemTime(SERVER - 1000);
    serve(SERVER - 1000);
    await syncServerClock(freshHost());
    serve(SERVER);
    await syncServerClock(freshHost());
    expect(serverClockOffset()).toBe(2600);
  });

  it("starts again from a header that contradicts the earlier ones", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    const host = freshHost();
    serve(SERVER);
    await syncServerClock(host);
    // The client's clock is corrected.
    vi.setSystemTime(SERVER);
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(0);
  });

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
        return { headers: { get: () => header(SERVER + 3_600_000) } };
      }),
    );
    await syncServerClock(host);
    expect(serverClockOffset()).toBe(held);
  });

  it("gives up on a request that never answers, so a later connection measures", async () => {
    // AbortSignal.timeout runs outside the fake timers.
    const expired = new AbortController();
    const timeout = vi.spyOn(AbortSignal, "timeout").mockReturnValue(expired.signal);
    const fetch = vi.fn(
      (_: string, init: RequestInit) =>
        new Promise((_, reject) => init.signal?.addEventListener("abort", () => reject(new Error("timed out")))),
    );
    vi.stubGlobal("fetch", fetch);
    const host = freshHost();
    const hung = syncServerClock(host);
    expect(timeout).toHaveBeenCalledWith(5_000);
    expired.abort();
    await hung;
    timeout.mockRestore();
    serve(SERVER);
    vi.setSystemTime(SERVER + MINUTES_3);
    await syncServerClock(host);
    expect(serverClockOffset()).not.toBe(0);
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
    constructor(readonly url: string) {
      FakeSocket.opened.push(this);
    }
  }

  it.each([
    ["sessions", "sessions", (host: string) => connectSessionsSocket(host, "t k", () => {})],
    ["session", "sessions/s1", (host: string) => connectSessionSocket(host, "t k", "s1", () => {})],
    ["inbox", "inbox/i1", (host: string) => connectInboxSocket(host, "t k", "i1", () => {})],
  ])("measures the clock when the %s socket opens", (_, path, connect) => {
    vi.stubGlobal("WebSocket", FakeSocket);
    const fetch = serve(SERVER);
    const host = freshHost();
    connect(host);
    expect(FakeSocket.opened.at(-1)?.url).toBe(`${host.replace("http", "ws")}/ws/${path}?token=t%20k`);
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

  it("counts account windows and queued schedules from the server's clock", () => {
    const inAnHour = new Date(SERVER + 3_600_000).toISOString();
    const bucket = {
      account_label: "Max",
      snapshot: { windows: [{ id: "w", label: "5h", used_percent: 10, resets_at: inAnHour }] },
    } as UsageDashboardBucket;
    expect(accountWindows(bucket)[0].resetsIn).toBe("1h");
    expect(mostConstrainedWindow([bucket])?.resetsIn).toBe("1h");

    const soon = new Date(SERVER + 600_000).toISOString();
    expect(formatShortWhen(soon)).toBe("in 10m");
    const schedule = { id: "s", status: "pending", scheduled_at: soon, cwd: "/", backend: "codex" } as ScheduledSession;
    expect(summarizeScheduled([schedule], [], new Map()).items[0].whenLabel).toBe("in 10m");
  });

  it("ages a thread and a send time from the server's clock", () => {
    const earlier = new Date(SERVER - 600_000).toISOString();
    expect(formatThreadTime(earlier)).toBe(formatThreadTime(earlier, SERVER));
    expect(relativeFromNow(new Date(SERVER + 600_000))).toBe("in 10 min");
  });

  it("takes today from the server's clock", async () => {
    // A minute from midnight, on the side the client's own clock has not reached or has left.
    const server = new Date(2026, 10, 28).getTime() - Math.sign(skew) * 60_000;
    vi.setSystemTime(server + skew);
    serve(server);
    await syncServerClock(freshHost());
    const written = new Date(server - 30_000);
    const item = { updated_at: written.toISOString() } as InboxItem;
    expect(updatedToday([item])).toEqual([item]);
    expect(formatInboxTime(item.updated_at)).toMatch(/^Today at /);
    expect(formatClock(written)).toBe(written.toLocaleString(undefined, { hour: "numeric", minute: "2-digit" }));
  });

  it("previews a picked time by the server's clock and a delay by itself", () => {
    const picked = new Date(SERVER + 600_000);
    const local = new Date(picked.getTime() - picked.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
    expect(schedulePreview("datetime", "", local, new Date())?.relative).toBe("in 10 min");
    expect(schedulePreview("delay", "15", "", new Date())?.relative).toBe("in 15 min");
  });
});
