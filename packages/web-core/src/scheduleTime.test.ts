import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatClock, formatRelative } from "./scheduleTime";

const NOW = new Date(2026, 10, 24, 12, 0, 0);

describe("formatRelative", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  const from = (offsetMs: number) => formatRelative(new Date(NOW.getTime() + offsetMs));
  const MIN = 60_000;
  const HOUR = 60 * MIN;

  it.each([
    [-5_000, "any moment"],
    [0, "any moment"],
    [10_000, "in <1m"],
    [30_000, "in 1m"],
    [45 * MIN, "in 45m"],
    [59 * MIN + 29_000, "in 59m"],
    [90 * MIN, "in 2h"],
    [24 * HOUR, "in 24h"],
    [47 * HOUR, "in 47h"],
    [72 * HOUR, "in 3d"],
    [10 * 24 * HOUR, "in 10d"],
  ])("renders %d ms ahead as %j", (offset, label) => {
    expect(from(offset)).toBe(label);
  });
});

describe("formatClock", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  it("shows only the time for a target later the same day", () => {
    const target = new Date(2026, 10, 24, 18, 30);
    expect(formatClock(target)).toBe(
      target.toLocaleString(undefined, { hour: "numeric", minute: "2-digit" }),
    );
  });

  it("adds the month and day for another day", () => {
    const target = new Date(2026, 10, 25, 9, 5);
    expect(formatClock(target)).toBe(
      target.toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }),
    );
    expect(formatClock(target)).not.toBe(
      target.toLocaleString(undefined, { hour: "numeric", minute: "2-digit" }),
    );
  });
});
