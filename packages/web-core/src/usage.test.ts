import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { UsageWindow } from "./types";
import {
  clampPercent,
  formatRateLimitWindowReset,
  formatRateLimitWindowResetShort,
  formatRateLimitWindowTokens,
  formatRelativeTime,
  formatTokens,
  rateLimitUsageTone,
  rateLimitWindowPercent,
  usageTone,
} from "./usage";

const NOW = Date.parse("2026-11-24T12:00:00Z");
const window = (overrides: Partial<UsageWindow> = {}): UsageWindow => ({
  id: "5h",
  label: "5h",
  used_percent: 50,
  ...overrides,
});
const at = (offsetMs: number) => new Date(NOW + offsetMs).toISOString();

describe("usage formatting", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(NOW);
  });
  afterEach(() => vi.useRealTimers());

  describe("formatTokens", () => {
    it("groups thousands", () => {
      expect(formatTokens(0)).toBe("0");
      expect(formatTokens(1234567)).toBe("1,234,567");
    });
  });

  describe("formatRelativeTime", () => {
    it("reports unparsable input as Unknown", () => {
      expect(formatRelativeTime("not-a-date")).toBe("Unknown");
      expect(formatRelativeTime("")).toBe("Unknown");
    });

    it("uses seconds, minutes, hours and days with natural phrasing", () => {
      expect(formatRelativeTime(at(0))).toBe("now");
      expect(formatRelativeTime(at(30_000))).toBe("in 30 seconds");
      expect(formatRelativeTime(at(-5 * 60_000))).toBe("5 minutes ago");
      expect(formatRelativeTime(at(3 * 3_600_000))).toBe("in 3 hours");
      expect(formatRelativeTime(at(-86_400_000))).toBe("yesterday");
      expect(formatRelativeTime(at(86_400_000))).toBe("tomorrow");
      expect(formatRelativeTime(at(5 * 86_400_000))).toBe("in 5 days");
    });

    it("switches unit at the 60 second, 60 minute and 24 hour boundaries", () => {
      expect(formatRelativeTime(at(59_000))).toBe("in 59 seconds");
      expect(formatRelativeTime(at(60_000))).toBe("in 1 minute");
      expect(formatRelativeTime(at(3_599_000))).toBe("in 60 minutes");
      expect(formatRelativeTime(at(3_600_000))).toBe("in 1 hour");
    });
  });

  describe("formatRateLimitWindowResetShort", () => {
    it("renders a terse future and past label", () => {
      expect(formatRateLimitWindowResetShort(window({ resets_at: at(4 * 3_600_000) }))).toBe("in 4h");
      expect(formatRateLimitWindowResetShort(window({ resets_at: at(-3 * 86_400_000) }))).toBe("3d ago");
      expect(formatRateLimitWindowResetShort(window({ resets_at: at(90_000) }))).toBe("in 2m");
      expect(formatRateLimitWindowResetShort(window({ resets_at: at(20_000) }))).toBe("in 20s");
    });

    it("says now at the reset instant", () => {
      expect(formatRateLimitWindowResetShort(window({ resets_at: at(0) }))).toBe("now");
    });

    it("is null for an unparsable reset time", () => {
      expect(formatRateLimitWindowResetShort(window({ resets_at: "nope" }))).toBeNull();
    });

    it("falls back to the backend description without a reset time", () => {
      expect(formatRateLimitWindowResetShort(window({ reset_description: "Resets Monday" }))).toBe("Resets Monday");
      expect(formatRateLimitWindowResetShort(window())).toBeNull();
    });
  });

  describe("formatRateLimitWindowReset", () => {
    it("prefers the machine-readable reset time over the description", () => {
      expect(
        formatRateLimitWindowReset(window({ resets_at: at(2 * 3_600_000), reset_description: "later" })),
      ).toBe("in 2 hours");
      expect(formatRateLimitWindowReset(window({ reset_description: "later" }))).toBe("later");
      expect(formatRateLimitWindowReset(window())).toBeNull();
    });
  });
});

describe("clampPercent", () => {
  it("clamps to 0..100 and passes null through", () => {
    expect(clampPercent(null)).toBeNull();
    expect(clampPercent(-5)).toBe(0);
    expect(clampPercent(42)).toBe(42);
    expect(clampPercent(140)).toBe(100);
  });
});

describe("usageTone", () => {
  it.each([
    [null, "good"],
    [0, "good"],
    [69, "good"],
    [70, "warn"],
    [89, "warn"],
    [90, "danger"],
    [100, "danger"],
  ] as const)("maps %s to %s", (percent, tone) => {
    expect(usageTone(percent)).toBe(tone);
  });
});

describe("rateLimitWindowPercent", () => {
  it("rounds and clamps the used percentage", () => {
    expect(rateLimitWindowPercent(window({ used_percent: 41.6 }))).toBe(42);
    expect(rateLimitWindowPercent(window({ used_percent: 250 }))).toBe(100);
    expect(rateLimitWindowPercent(window({ used_percent: 0 }))).toBe(0);
  });

  it("is null for a negative or non-finite value", () => {
    expect(rateLimitWindowPercent(window({ used_percent: -1 }))).toBeNull();
    expect(rateLimitWindowPercent(window({ used_percent: Number.NaN }))).toBeNull();
    expect(rateLimitWindowPercent(window({ used_percent: Infinity }))).toBeNull();
  });
});

describe("formatRateLimitWindowTokens", () => {
  it("is null without any token fields", () => {
    expect(formatRateLimitWindowTokens(window())).toBeNull();
    expect(formatRateLimitWindowTokens(window({ used_tokens: null, remaining_tokens: null }))).toBeNull();
  });

  it("joins used, left and limit with the limit shown only alongside used", () => {
    expect(
      formatRateLimitWindowTokens(window({ used_tokens: 1500, remaining_tokens: 500, limit_tokens: 2000 })),
    ).toBe("1,500 used · 500 left · of 2,000");
    expect(formatRateLimitWindowTokens(window({ remaining_tokens: 500, limit_tokens: 2000 }))).toBe("500 left");
    expect(formatRateLimitWindowTokens(window({ used_tokens: 0 }))).toBe("0 used");
  });
});

describe("rateLimitUsageTone", () => {
  it("is good without usage or windows", () => {
    expect(rateLimitUsageTone(null)).toBe("good");
    expect(rateLimitUsageTone({ windows: [] })).toBe("good");
  });

  it("uses the worst window and ignores windows without a valid percentage", () => {
    expect(rateLimitUsageTone({ windows: [window({ used_percent: 10 }), window({ used_percent: 75 })] })).toBe("warn");
    expect(rateLimitUsageTone({ windows: [window({ used_percent: 95 }), window({ used_percent: 10 })] })).toBe("danger");
    expect(rateLimitUsageTone({ windows: [window({ used_percent: -1 })] })).toBe("good");
  });
});
