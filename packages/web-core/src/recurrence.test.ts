import { afterEach, describe, expect, it, vi } from "vitest";

import {
  WEEKDAY_LABELS,
  browserTimezone,
  cronFromState,
  cronToLabel,
  defaultRecurrenceState,
  formatInZone,
  isIdle,
  isRecurring,
  recurrenceSentence,
  supportedTimezones,
  timezoneAbbrev,
  type RecurrenceState,
} from "./recurrence";

const state = (overrides: Partial<RecurrenceState> = {}): RecurrenceState => ({
  cadence: "daily",
  startAt: "2026-11-24T09:30",
  weekday: 1,
  dayOfMonth: 15,
  customCron: "0 9 * * 1-5",
  timezone: "UTC",
  ...overrides,
});

describe("cronFromState", () => {
  it("builds each preset from the start time", () => {
    expect(cronFromState(state({ cadence: "daily" }))).toBe("30 9 * * *");
    expect(cronFromState(state({ cadence: "weekdays" }))).toBe("30 9 * * 1-5");
    expect(cronFromState(state({ cadence: "weekly", weekday: 3 }))).toBe("30 9 * * 3");
    expect(cronFromState(state({ cadence: "monthly", dayOfMonth: 31 }))).toBe("30 9 31 * *");
  });

  it("does not zero-pad the numeric fields", () => {
    expect(cronFromState(state({ startAt: "2026-11-24T00:05" }))).toBe("5 0 * * *");
  });

  it("passes a custom expression through, trimmed", () => {
    expect(cronFromState(state({ cadence: "custom", customCron: "  */5 * * * *  " }))).toBe("*/5 * * * *");
  });

  it("is null for an empty custom expression", () => {
    expect(cronFromState(state({ cadence: "custom", customCron: "   " }))).toBeNull();
  });

  it("is null when a preset has no valid time", () => {
    expect(cronFromState(state({ startAt: "" }))).toBeNull();
    expect(cronFromState(state({ startAt: "2026-11-24" }))).toBeNull();
    expect(cronFromState(state({ startAt: "2026-11-24T24:00" }))).toBeNull();
    expect(cronFromState(state({ startAt: "2026-11-24T09:60" }))).toBeNull();
  });

  it("ignores the start time for the custom cadence", () => {
    expect(cronFromState(state({ cadence: "custom", startAt: "" }))).toBe("0 9 * * 1-5");
  });
});

describe("cronToLabel", () => {
  it.each([
    ["30 9 * * *", "UTC", "DAILY · 09:30 · UTC"],
    ["0 9 * * 1-5", "America/New_York", "WEEKDAYS · 09:00 · America/New_York"],
    ["5 18 * * 0", null, "WEEKLY SUN · 18:05"],
    ["0 9 * * 6", undefined, "WEEKLY SAT · 09:00"],
    ["0 9 15 * *", "UTC", "MONTHLY 15 · 09:00 · UTC"],
  ])("labels the preset shape %j", (cron, zone, label) => {
    expect(cronToLabel(cron, zone)).toBe(label);
  });

  it("falls back to CUSTOM for other five-field expressions", () => {
    expect(cronToLabel("*/5 * * * *", "UTC")).toBe("CUSTOM */5 * * * * · UTC");
    expect(cronToLabel("0 9 1 6 *")).toBe("CUSTOM 0 9 1 6 *");
    expect(cronToLabel("0 9 * * 1,3")).toBe("CUSTOM 0 9 * * 1,3");
    expect(cronToLabel("0 9 15 * 1")).toBe("CUSTOM 0 9 15 * 1");
  });

  it("labels expressions that are not five fields as raw CRON", () => {
    expect(cronToLabel("0 0 9 * * *", "UTC")).toBe("CRON 0 0 9 * * * · UTC");
    expect(cronToLabel("@daily")).toBe("CRON @daily");
  });

  it("tolerates extra whitespace", () => {
    expect(cronToLabel("  0   9  * * *  ")).toBe("DAILY · 09:00");
  });

  it("is empty without a cron", () => {
    expect(cronToLabel("")).toBe("");
    expect(cronToLabel(null, "UTC")).toBe("");
    expect(cronToLabel(undefined)).toBe("");
  });
});

describe("recurrenceSentence", () => {
  it("reads a cron as a sentence", () => {
    expect(recurrenceSentence("0 2 * * *", "UTC", "UTC")).toBe("Daily at 02:00");
    expect(recurrenceSentence("30 9 * * 1-5", "Asia/Singapore", "UTC")).toBe("Weekdays at 09:30 (Asia/Singapore)");
    expect(recurrenceSentence("0 9 * * 1", null, "UTC")).toBe("Mondays at 09:00");
    expect(recurrenceSentence("0 9 3 * *", null, "UTC")).toBe("Monthly on day 3 at 09:00");
    expect(recurrenceSentence("*/5 * * * *", null, "UTC")).toBe("Cron */5 * * * *");
  });
});

describe("isRecurring and isIdle", () => {
  it("is recurring only with a non-empty cron", () => {
    expect(isRecurring({ cron: "0 9 * * *" })).toBe(true);
    expect(isRecurring({ cron: "" })).toBe(false);
    expect(isRecurring({ cron: null })).toBe(false);
    expect(isRecurring({})).toBe(false);
    expect(isRecurring(null)).toBe(false);
    expect(isRecurring(undefined)).toBe(false);
  });

  it("is idle only for the idle trigger", () => {
    expect(isIdle({ trigger: "idle" })).toBe(true);
    expect(isIdle({ trigger: "time" })).toBe(false);
    expect(isIdle({})).toBe(false);
    expect(isIdle(null)).toBe(false);
  });
});

describe("constants and defaults", () => {
  it("labels the weekdays from Sunday, matching cron day-of-week", () => {
    expect(WEEKDAY_LABELS).toHaveLength(7);
    expect(WEEKDAY_LABELS[0]).toBe("Sun");
  });

  it("defaults to a daily 09:00 recurrence today in the browser zone", () => {
    const defaults = defaultRecurrenceState();
    expect(defaults.cadence).toBe("daily");
    expect(defaults.startAt).toMatch(/^\d{4}-\d{2}-\d{2}T09:00$/);
    expect(defaults.customCron).toBe("0 9 * * 1-5");
    expect(defaults.timezone).toBe(browserTimezone());
    expect(cronFromState(defaults)).toBe("0 9 * * *");
  });
});

describe("timezones", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reports a non-empty browser zone", () => {
    expect(browserTimezone().length).toBeGreaterThan(0);
  });

  it("lists the browser zone first and exactly once", () => {
    const zones = supportedTimezones();
    expect(zones[0]).toBe(browserTimezone());
    expect(zones.filter((z) => z === zones[0])).toHaveLength(1);
  });

  it("abbreviates a zone for a given instant", () => {
    const summer = new Date("2026-07-01T12:00:00Z");
    const winter = new Date("2026-01-01T12:00:00Z");
    expect(timezoneAbbrev(summer, "America/New_York")).toBe("EDT");
    expect(timezoneAbbrev(winter, "America/New_York")).toBe("EST");
    expect(timezoneAbbrev(summer, "UTC")).toBe("UTC");
  });

  it("falls back to the zone name for an unknown zone", () => {
    expect(timezoneAbbrev(new Date(), "Not/AZone")).toBe("Not/AZone");
  });

  it("formats an instant in the requested zone", () => {
    const instant = new Date("2026-07-21T09:00:00Z");
    expect(formatInZone(instant, "UTC")).toMatch(/09:00|9:00/);
    expect(formatInZone(instant, "Asia/Singapore")).toMatch(/05:00|5:00|17:00/);
  });

  it("falls back to the local format for an unknown zone", () => {
    const instant = new Date("2026-07-21T09:00:00Z");
    expect(formatInZone(instant, "Not/AZone")).toBe(instant.toLocaleString());
  });
});
