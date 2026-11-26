import { describe, expect, it } from "vitest";

import { defaultRecurrenceState } from "../recurrence";
import { withScheduleTiming } from "./schedule";

describe("withScheduleTiming", () => {
  const request = { backend: "codex", cwd: "/w" };
  const form = { timingMode: "once" as const, recurrence: defaultRecurrenceState(), timing: "delay" as const, delay: "15", at: "" };

  it("fills a delay, a time or a recurrence", () => {
    expect(withScheduleTiming(request, form)).toEqual({ payload: { ...request, delay_seconds: 900 } });
    const at = withScheduleTiming(request, { ...form, timing: "datetime", at: "2030-01-01T09:00" });
    expect("payload" in at && at.payload.scheduled_at).toBe(new Date("2030-01-01T09:00").toISOString());
    const repeat = withScheduleTiming(request, {
      ...form,
      timingMode: "repeat",
      recurrence: { ...defaultRecurrenceState(), cadence: "weekdays", startAt: "2030-01-01T09:30", timezone: "UTC" },
    });
    expect(repeat).toEqual({ payload: { ...request, cron: "30 9 * * 1-5", timezone: "UTC", start_at: "2030-01-01T09:30" } });
  });

  it("explains invalid timing", () => {
    expect(withScheduleTiming(request, { ...form, delay: "-1" })).toEqual({ error: "Enter a non-negative delay in minutes." });
    expect(withScheduleTiming(request, { ...form, timing: "datetime", at: "nope" })).toEqual({ error: "Enter a valid scheduled time." });
    expect(
      withScheduleTiming(request, { ...form, timingMode: "repeat", recurrence: { ...defaultRecurrenceState(), cadence: "custom", customCron: " " } }),
    ).toEqual({ error: "Enter a valid recurrence." });
  });
});
