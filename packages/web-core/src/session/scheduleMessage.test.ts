import { describe, expect, it } from "vitest";

import { defaultRecurrenceState } from "../recurrence";
import {
  defaultScheduledAt,
  relativeFromNow,
  scheduleMessageOptions,
  schedulePreview,
  type ScheduleMessageForm,
} from "./scheduleMessage";

const NOW = new Date(2026, 0, 1, 12, 0, 0);

const form = (patch: Partial<ScheduleMessageForm> = {}): ScheduleMessageForm => ({
  trigger: "time",
  idleBatch: "next_cycle",
  timingMode: "once",
  recurrence: { ...defaultRecurrenceState(), startAt: "2026-01-02T09:30", timezone: "UTC" },
  timing: "delay",
  delay: "15",
  at: "2026-01-01T13:00",
  submit: true,
  ...patch,
});

describe("defaultScheduledAt", () => {
  it("is a datetime-local value a quarter of an hour ahead", () => {
    expect(defaultScheduledAt(new Date(2026, 0, 1, 12, 50))).toBe("2026-01-01T13:05");
  });
});

describe("relativeFromNow", () => {
  const ahead = (minutes: number) => new Date(NOW.getTime() + minutes * 60_000);
  it("rounds to the unit that reads", () => {
    expect(relativeFromNow(ahead(-1), NOW.getTime())).toBe("now");
    expect(relativeFromNow(ahead(0.2), NOW.getTime())).toBe("in under a minute");
    expect(relativeFromNow(ahead(5), NOW.getTime())).toBe("in 5 min");
    expect(relativeFromNow(ahead(90), NOW.getTime())).toBe("in 1h 30m");
    expect(relativeFromNow(ahead(120), NOW.getTime())).toBe("in 2h");
    expect(relativeFromNow(ahead(60 * 72), NOW.getTime())).toBe("in 3 days");
  });
});

describe("schedulePreview", () => {
  it("is null for an invalid delay or time", () => {
    expect(schedulePreview("delay", "-1", "", NOW)).toBeNull();
    expect(schedulePreview("delay", "abc", "", NOW)).toBeNull();
    expect(schedulePreview("datetime", "15", "", NOW)).toBeNull();
  });
  it("names when a delay lands", () => {
    expect(schedulePreview("delay", "15", "", NOW)?.relative).toBe("in 15 min");
  });
});

describe("scheduleMessageOptions", () => {
  it("sends an idle batch without a time", () => {
    expect(scheduleMessageOptions(form({ trigger: "idle", idleBatch: "with_previous", submit: false }))).toEqual({
      options: { submit: false, trigger: "idle", idleBatchMode: "with_previous" },
    });
  });
  it("converts a delay to seconds and a local time to an instant", () => {
    expect(scheduleMessageOptions(form({ delay: "0.5" }))).toEqual({ options: { submit: true, delaySeconds: 30 } });
    expect(scheduleMessageOptions(form({ timing: "datetime" }))).toEqual({
      options: { submit: true, scheduledAt: new Date("2026-01-01T13:00").toISOString() },
    });
  });
  it("builds a recurrence with its zone and start", () => {
    expect(scheduleMessageOptions(form({ timingMode: "repeat" }))).toEqual({
      options: { submit: true, cron: "30 9 * * *", timezone: "UTC", startAt: "2026-01-02T09:30" },
    });
  });
  it("names what is invalid", () => {
    const empty = form({ timingMode: "repeat" });
    empty.recurrence = { ...empty.recurrence, cadence: "custom", customCron: " " };
    expect(scheduleMessageOptions(empty)).toEqual({ error: "Enter a valid recurrence." });
    expect(scheduleMessageOptions(form({ delay: "-2" }))).toEqual({
      error: "Enter a non-negative delay in minutes.",
    });
    expect(scheduleMessageOptions(form({ timing: "datetime", at: "" }))).toEqual({
      error: "Enter a valid scheduled time.",
    });
  });
});
