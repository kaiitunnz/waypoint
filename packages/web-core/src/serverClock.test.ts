import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  estimateClockOffset,
  serverClockOffset,
  serverNow,
  subscribeServerClock,
  syncServerClock,
} from "./serverClock";
import { formatElapsed } from "./session/workingRow";
import { formatSessionTime } from "./sessionPresentation";
import { formatRelativeTime } from "./usage";
import { formatResetsIn } from "./usageSummary";

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

  it("starts a new host from no offset and takes a hidden date as agreeing", async () => {
    vi.setSystemTime(SERVER + MINUTES_3);
    serve(SERVER);
    await syncServerClock(freshHost());
    expect(serverClockOffset()).not.toBe(0);
    serve(null);
    await syncServerClock(freshHost());
    expect(serverClockOffset()).toBe(0);
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

  it("counts a reset down from the server's clock", () => {
    expect(formatResetsIn(new Date(SERVER + 3_600_000).toISOString())).toBe("1h");
  });
});
