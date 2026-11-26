import { cronFromState, type RecurrenceState, type TimingMode } from "../recurrence";
import type { OneTimeTiming } from "../session/scheduleMessage";
import type { ScheduleCreateRequest } from "../types";

export interface ScheduleTimingForm {
  timingMode: TimingMode;
  recurrence: RecurrenceState;
  timing: OneTimeTiming;
  // Minutes from now, as typed.
  delay: string;
  // A `datetime-local` value.
  at: string;
}

// The request with its timing filled in, or the reason the timing is invalid.
export function withScheduleTiming(
  request: ScheduleCreateRequest,
  form: ScheduleTimingForm,
): { payload: ScheduleCreateRequest } | { error: string } {
  const payload = { ...request };
  if (form.timingMode === "repeat") {
    const cron = cronFromState(form.recurrence);
    if (!cron) return { error: "Enter a valid recurrence." };
    payload.cron = cron;
    payload.timezone = form.recurrence.timezone;
    payload.start_at = form.recurrence.startAt || null;
  } else if (form.timing === "delay") {
    const minutes = Number.parseFloat(form.delay);
    if (!Number.isFinite(minutes) || minutes < 0) return { error: "Enter a non-negative delay in minutes." };
    payload.delay_seconds = Math.round(minutes * 60);
  } else {
    const local = new Date(form.at);
    if (Number.isNaN(local.getTime())) return { error: "Enter a valid scheduled time." };
    payload.scheduled_at = local.toISOString();
  }
  return { payload };
}
