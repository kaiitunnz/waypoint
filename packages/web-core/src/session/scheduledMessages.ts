import { isIdle } from "../recurrence";
import type { MessageSchedule } from "../types";

export function describeScheduledDelivery(
  schedule: MessageSchedule,
  now: number = Date.now(),
): string {
  // An idle schedule delivers on session state, not a clock; its scheduled_at
  // is only the enqueue time, so it must never render as a countdown.
  if (isIdle(schedule)) {
    return "When idle";
  }
  if (!schedule.scheduled_at) {
    return "";
  }
  const diff = new Date(schedule.scheduled_at).getTime() - now;
  if (diff <= 0) {
    return "any moment";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) {
    return "in <1m";
  }
  if (minutes < 60) {
    return `in ${minutes}m`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `in ${days}d`;
}

export function scheduledAtMs(m: MessageSchedule): number {
  return m.scheduled_at
    ? new Date(m.scheduled_at).getTime()
    : Number.POSITIVE_INFINITY;
}

// Soonest first, undated last, so messages[0] is a robust "next" regardless of
// backend ordering.
export function pendingSchedulesSorted(all: MessageSchedule[]): MessageSchedule[] {
  return all
    .filter((m) => m.status === "pending")
    .sort((a, b) => scheduledAtMs(a) - scheduledAtMs(b));
}

export function sameSchedules(a: MessageSchedule[], b: MessageSchedule[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  return a.every((m, i) => {
    const other = b[i];
    return (
      m.id === other.id &&
      m.status === other.status &&
      m.scheduled_at === other.scheduled_at &&
      m.cron === other.cron &&
      m.last_run_at === other.last_run_at
    );
  });
}

// Poll fast when the soonest delivery is imminent or already past (so a
// just-fired message clears within a few seconds instead of lingering on "any
// moment"), and slowly otherwise.
export function schedulePollIntervalMs(
  messages: MessageSchedule[],
  now: number = Date.now(),
): number {
  const soonest = messages.reduce((min, m) => {
    if (!m.scheduled_at) {
      return min;
    }
    return Math.min(min, new Date(m.scheduled_at).getTime());
  }, Number.POSITIVE_INFINITY);
  const dueSoon = soonest - now <= 30_000;
  return dueSoon ? 4_000 : 15_000;
}
