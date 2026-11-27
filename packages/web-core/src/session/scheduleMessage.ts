import type { createMessageSchedule } from "../api";
import { cronFromState, type RecurrenceState, type TimingMode } from "../recurrence";
import { serverNow, toServerTime } from "../serverClock";
import type { IdleBatchMode } from "./sendModes";

export type OneTimeTiming = "delay" | "datetime";
export type ScheduleTrigger = "time" | "idle";

export const DELAY_PRESETS: readonly { label: string; minutes: number }[] = [
  { label: "5m", minutes: 5 },
  { label: "15m", minutes: 15 },
  { label: "30m", minutes: 30 },
  { label: "1h", minutes: 60 },
  { label: "3h", minutes: 180 },
];

const DEFAULT_LEAD_MINUTES = 15;

// A `datetime-local` value a quarter of an hour ahead.
export function defaultScheduledAt(now: Date = new Date()): string {
  const date = new Date(now.getTime());
  date.setMinutes(date.getMinutes() + DEFAULT_LEAD_MINUTES);
  const pad = (value: number) => value.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

export function relativeFromNow(target: Date, now: number = serverNow()): string {
  const diff = target.getTime() - now;
  if (diff <= 0) {
    return "now";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) {
    return "in under a minute";
  }
  if (minutes < 60) {
    return `in ${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  if (hours < 48) {
    return rem ? `in ${hours}h ${rem}m` : `in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `in ${days} days`;
}

// When a one-time message would be sent, or null while the input is invalid.
export function schedulePreview(
  timing: OneTimeTiming,
  delay: string,
  at: string,
  now: Date = new Date(),
): { absolute: string; relative: string } | null {
  let target: Date;
  if (timing === "delay") {
    const minutes = Number.parseFloat(delay);
    if (!Number.isFinite(minutes) || minutes < 0) {
      return null;
    }
    target = new Date(now.getTime() + minutes * 60_000);
  } else {
    target = new Date(at);
    if (Number.isNaN(target.getTime())) {
      return null;
    }
  }
  const sameDay = now.toDateString() === target.toDateString();
  const absolute = target.toLocaleString(undefined, {
    weekday: sameDay ? undefined : "short",
    month: sameDay ? undefined : "short",
    day: sameDay ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  // The server sends a picked time when its own clock reaches it; a delay runs from now.
  const from = timing === "delay" ? now.getTime() : toServerTime(now.getTime());
  return { absolute, relative: relativeFromNow(target, from) };
}

export interface ScheduleMessageForm {
  trigger: ScheduleTrigger;
  idleBatch: IdleBatchMode;
  timingMode: TimingMode;
  recurrence: RecurrenceState;
  timing: OneTimeTiming;
  delay: string;
  at: string;
  submit: boolean;
}

type ScheduleMessageOptions = NonNullable<Parameters<typeof createMessageSchedule>[4]>;

// The request options for a filled form, or the reason it cannot be sent.
export function scheduleMessageOptions(
  form: ScheduleMessageForm,
): { options: ScheduleMessageOptions } | { error: string } {
  const options: ScheduleMessageOptions = { submit: form.submit };
  if (form.trigger === "idle") {
    options.trigger = "idle";
    options.idleBatchMode = form.idleBatch;
  } else if (form.timingMode === "repeat") {
    const cron = cronFromState(form.recurrence);
    if (!cron) {
      return { error: "Enter a valid recurrence." };
    }
    options.cron = cron;
    options.timezone = form.recurrence.timezone;
    if (form.recurrence.startAt) options.startAt = form.recurrence.startAt;
  } else if (form.timing === "delay") {
    const minutes = Number.parseFloat(form.delay);
    if (!Number.isFinite(minutes) || minutes < 0) {
      return { error: "Enter a non-negative delay in minutes." };
    }
    options.delaySeconds = Math.round(minutes * 60);
  } else {
    const local = new Date(form.at);
    if (Number.isNaN(local.getTime())) {
      return { error: "Enter a valid scheduled time." };
    }
    options.scheduledAt = local.toISOString();
  }
  return { options };
}
