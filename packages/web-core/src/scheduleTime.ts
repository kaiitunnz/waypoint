/** Clock and countdown formatting for scheduled sessions and messages. */

import { WEEKDAY_LABELS } from "./recurrence";
import { serverNow } from "./serverClock";

const DAY_MS = 86_400_000;

const pad = (value: number) => String(value).padStart(2, "0");
const clock = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

// "today at 07:00", "Tue 02:00" within a week either way, else "Jul 21 09:00".
export function dayClock(date: Date, now: Date): string {
  const days = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (days === 0) return `today at ${clock(date)}`;
  if (Math.abs(days) < 7) return `${WEEKDAY_LABELS[date.getDay()]} ${clock(date)}`;
  return `${date.toLocaleDateString("en-US", { month: "short", day: "numeric" })} ${clock(date)}`;
}

// "in 7h 18m" while under two days, else whole days; "now" once due.
export function countdown(target: Date, now: Date): string {
  const minutes = Math.ceil((target.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return minutes % 60 ? `in ${hours}h ${minutes % 60}m` : `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}

export function formatClock(target: Date): string {
  const sameDay = new Date(serverNow()).toDateString() === target.toDateString();
  return target.toLocaleString(undefined, {
    month: sameDay ? undefined : "short",
    day: sameDay ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

export function formatRelative(target: Date): string {
  const diff = target.getTime() - serverNow();
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
