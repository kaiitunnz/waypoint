import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { MessageSchedule } from "../types";
import {
  describeScheduledDelivery,
  pendingSchedulesSorted,
  sameSchedules,
  schedulePollIntervalMs,
} from "./scheduledMessages";

const NOW = Date.parse("2026-11-24T12:00:00Z");
const MIN = 60_000;
const HOUR = 60 * MIN;
const DAY = 24 * HOUR;

function schedule(overrides: Partial<MessageSchedule> = {}): MessageSchedule {
  return {
    id: "s",
    session_id: "sess",
    text: "t",
    submit: true,
    status: "pending",
    created_at: "2026-11-24T00:00:00Z",
    ...overrides,
  };
}

const at = (offsetMs: number, overrides: Partial<MessageSchedule> = {}) =>
  schedule({ scheduled_at: new Date(NOW + offsetMs).toISOString(), ...overrides });

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

describe("describeScheduledDelivery", () => {
  it("counts down a dated delivery through formatRelative", () => {
    expect(describeScheduledDelivery(at(HOUR))).toBe("in 1h");
  });

  it("shows an idle schedule as waiting on session state, never a countdown", () => {
    expect(describeScheduledDelivery(at(HOUR, { trigger: "idle" }))).toBe("When idle");
    expect(describeScheduledDelivery(schedule({ trigger: "idle" }))).toBe("When idle");
  });

  it("is blank for an undated schedule", () => {
    expect(describeScheduledDelivery(schedule({ scheduled_at: null }))).toBe("");
    expect(describeScheduledDelivery(schedule())).toBe("");
  });
});

describe("pendingSchedulesSorted", () => {
  it("keeps only pending schedules, soonest first", () => {
    const sorted = pendingSchedulesSorted([
      at(3 * HOUR, { id: "c" }),
      at(HOUR, { id: "a" }),
      at(2 * HOUR, { id: "x", status: "sent" }),
      at(2 * HOUR, { id: "b" }),
    ]);
    expect(sorted.map((s) => s.id)).toEqual(["a", "b", "c"]);
  });

  it("puts undated schedules last", () => {
    const sorted = pendingSchedulesSorted([schedule({ id: "undated" }), at(HOUR, { id: "dated" })]);
    expect(sorted.map((s) => s.id)).toEqual(["dated", "undated"]);
  });

  it("keeps the input order for several undated schedules", () => {
    const sorted = pendingSchedulesSorted([schedule({ id: "u1" }), schedule({ id: "u2" })]);
    expect(sorted.map((s) => s.id)).toEqual(["u1", "u2"]);
  });

  it("does not mutate its input", () => {
    const input = [at(2 * HOUR, { id: "b" }), at(HOUR, { id: "a" })];
    pendingSchedulesSorted(input);
    expect(input.map((s) => s.id)).toEqual(["b", "a"]);
  });

  it("returns an empty list for no pending schedules", () => {
    expect(pendingSchedulesSorted([])).toEqual([]);
    expect(pendingSchedulesSorted([at(HOUR, { status: "sent" })])).toEqual([]);
  });
});

describe("sameSchedules", () => {
  it("is true for identical lists, including two empty ones", () => {
    const list = [at(HOUR, { id: "a" })];
    expect(sameSchedules(list, [...list])).toBe(true);
    expect(sameSchedules([], [])).toBe(true);
  });

  it("is false for different lengths or order", () => {
    const a = at(HOUR, { id: "a" });
    const b = at(2 * HOUR, { id: "b" });
    expect(sameSchedules([a], [a, b])).toBe(false);
    expect(sameSchedules([a, b], [b, a])).toBe(false);
  });

  it.each([
    ["status", { status: "sent" as const }],
    ["scheduled_at", { scheduled_at: "2030-01-01T00:00:00Z" }],
    ["cron", { cron: "0 9 * * *" }],
    ["last_run_at", { last_run_at: "2026-11-24T01:00:00Z" }],
  ])("notices a change to %s", (_field, change) => {
    const a = at(HOUR, { id: "a" });
    expect(sameSchedules([a], [{ ...a, ...change }])).toBe(false);
  });

  it("ignores text and failure_reason, which cannot change while a schedule is pending", () => {
    const a = at(HOUR, { id: "a" });
    expect(sameSchedules([a], [{ ...a, text: "edited", failure_reason: "x" }])).toBe(true);
  });
});

describe("schedulePollIntervalMs", () => {
  it("polls fast when the soonest delivery is due within 30 seconds or already past", () => {
    expect(schedulePollIntervalMs([at(-HOUR)])).toBe(4_000);
    expect(schedulePollIntervalMs([at(0)])).toBe(4_000);
    expect(schedulePollIntervalMs([at(30_000)])).toBe(4_000);
  });

  it("polls slowly otherwise", () => {
    expect(schedulePollIntervalMs([at(30_001)])).toBe(15_000);
    expect(schedulePollIntervalMs([at(DAY)])).toBe(15_000);
  });

  it("is decided by the soonest dated schedule", () => {
    expect(schedulePollIntervalMs([at(DAY), at(10_000)])).toBe(4_000);
  });

  it("polls slowly with no schedules or only undated ones", () => {
    expect(schedulePollIntervalMs([])).toBe(15_000);
    expect(schedulePollIntervalMs([schedule()])).toBe(15_000);
  });

  it("polls slowly when any date is unparsable, because the minimum becomes NaN", () => {
    expect(schedulePollIntervalMs([at(DAY), schedule({ scheduled_at: "not-a-date" })])).toBe(15_000);
    expect(schedulePollIntervalMs([at(0), schedule({ scheduled_at: "not-a-date" })])).toBe(15_000);
  });
});
