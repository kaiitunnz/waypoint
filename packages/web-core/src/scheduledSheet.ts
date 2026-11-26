import { humaniseBackend, permissionModeLabel, type BackendCatalog } from "./backends";
import { modelLabelFor } from "./modelDisplay";
import { cronCadence } from "./recurrence";
import { messageText } from "./session/heldMessages";
import type { MessageSchedule, ScheduledSession } from "./types";

const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const WEEKDAY_NAMES = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const DAY_MS = 86_400_000;

const pad = (value: number) => String(value).padStart(2, "0");
const clock = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`;
const startOfDay = (date: Date) => new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime();

// "today at 07:00", "Tue 02:00" within a week either way, else "Jul 21 09:00".
export function dayClock(date: Date, now: Date): string {
  const days = Math.round((startOfDay(date) - startOfDay(now)) / DAY_MS);
  if (days === 0) return `today at ${clock(date)}`;
  if (Math.abs(days) < 7) return `${WEEKDAYS[date.getDay()]} ${clock(date)}`;
  return `${MONTHS[date.getMonth()]} ${date.getDate()} ${clock(date)}`;
}

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

// "in 7h 18m" while under two days, else whole days; "now" once due.
export function countdown(target: Date, now: Date): string {
  const minutes = Math.ceil((target.getTime() - now.getTime()) / 60_000);
  if (minutes <= 0) return "now";
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return minutes % 60 ? `in ${hours}h ${minutes % 60}m` : `in ${hours}h`;
  return `in ${Math.round(hours / 24)}d`;
}

// "Daily at 02:00", "Weekdays at 09:00", "Mondays at 09:00", "Monthly on day 3 at
// 09:00", else the cron expression; the zone is named when it isn't `localZone`.
export function recurrenceSentence(cron: string, timezone: string | null | undefined, localZone: string): string {
  const zone = timezone && timezone !== localZone ? ` (${timezone})` : "";
  const cadence = cronCadence(cron);
  switch (cadence?.kind) {
    case "daily":
      return `Daily at ${cadence.clock}${zone}`;
    case "weekdays":
      return `Weekdays at ${cadence.clock}${zone}`;
    case "weekly":
      return `${WEEKDAY_NAMES[cadence.weekday]} at ${cadence.clock}${zone}`;
    case "monthly":
      return `Monthly on day ${cadence.day} at ${cadence.clock}${zone}`;
    default:
      return `Cron ${cron}${zone}`;
  }
}

export type ScheduledAction = "open" | "openLast" | "sendNow" | "cancel" | "dismiss";

export interface ScheduledRowView {
  key: string;
  recurring: boolean;
  title: string;
  // Time left until a pending run.
  countdown: string | null;
  // When it runs or what happened; `failed` shows in the error hue.
  state: string;
  failed: boolean;
  // A recurring row's previous run.
  lastRun: { text: string; failed: boolean } | null;
  actions: ScheduledAction[];
  // A resolved row reads quieter than a pending one.
  resolved: boolean;
}

export interface ScheduledLaunchView extends ScheduledRowView {
  // Agent, model and permission mode.
  agent: string;
  folder: string;
  sessionId: string | null;
}

export interface ScheduledMessageView extends ScheduledRowView {
  target: string;
  sessionId: string;
}

// Pending first, soonest first with when-idle last; then the rest, newest first.
export function sortScheduled<T extends { status: string; scheduled_at?: string | null; trigger?: string }>(items: T[]): T[] {
  const at = (item: T) => (item.scheduled_at ? Date.parse(item.scheduled_at) : 0);
  const pending = items.filter((item) => item.status === "pending");
  const rest = items.filter((item) => item.status !== "pending");
  pending.sort((a, b) => Number(a.trigger === "idle") - Number(b.trigger === "idle") || at(a) - at(b));
  rest.sort((a, b) => at(b) - at(a));
  return [...pending, ...rest];
}

function lastRun(record: { cron?: string | null; last_run_at?: string | null; last_run_status?: string | null; last_failure_reason?: string | null }, now: Date) {
  if (!record.cron || !record.last_run_at) return null;
  if (record.last_run_status === "failed") {
    return { text: `Last run failed: ${record.last_failure_reason || "unknown error"}`, failed: true };
  }
  return { text: `Last run ${dayClock(new Date(record.last_run_at), now)}, ${record.last_run_status ?? "done"}`, failed: false };
}

const folderName = (cwd: string) => cwd.replace(/\/+$/, "").split("/").pop() || cwd;

export function launchRowView(
  schedule: ScheduledSession,
  catalog: BackendCatalog,
  now: Date,
  localZone: string,
): ScheduledLaunchView {
  const recurring = Boolean(schedule.cron);
  const at = new Date(schedule.scheduled_at);
  const pending = schedule.status === "pending";
  let state: string;
  if (schedule.status === "failed") state = `Failed: ${schedule.failure_reason || "unknown error"}`;
  else if (schedule.status === "cancelled") state = "Cancelled";
  else if (schedule.status === "launched") state = `Launched ${dayClock(at, now)}`;
  else state = recurring ? recurrenceSentence(schedule.cron ?? "", schedule.timezone, localZone) : capitalize(dayClock(at, now));
  const agent = [
    humaniseBackend(schedule.backend, catalog),
    schedule.model ? modelLabelFor(schedule.model, []) : null,
    schedule.permission_mode ? (permissionModeLabel(schedule.backend, schedule.permission_mode, catalog) ?? schedule.permission_mode) : null,
  ]
    .filter(Boolean)
    .join(", ");
  let actions: ScheduledAction[];
  if (pending) actions = recurring && schedule.session_id ? ["openLast", "cancel"] : ["cancel"];
  else if (schedule.status === "launched" && schedule.session_id) actions = ["open", "dismiss"];
  else actions = ["dismiss"];
  return {
    key: `launch:${schedule.id}`,
    recurring,
    title: schedule.title || folderName(schedule.cwd),
    countdown: pending ? countdown(at, now) : null,
    state,
    failed: schedule.status === "failed",
    lastRun: lastRun(schedule, now),
    actions,
    resolved: !pending,
    agent,
    folder: schedule.cwd,
    sessionId: schedule.session_id ?? null,
  };
}

export function messageRowView(
  message: MessageSchedule,
  sessionTitle: string | null,
  now: Date,
  localZone: string,
): ScheduledMessageView {
  const recurring = Boolean(message.cron);
  const idle = message.trigger === "idle";
  const pending = message.status === "pending";
  const at = message.scheduled_at ? new Date(message.scheduled_at) : null;
  let state: string;
  if (message.status === "failed") state = `Failed: ${message.failure_reason || "unknown error"}`;
  else if (message.status === "cancelled") state = "Cancelled";
  else if (message.status === "sent") state = idle ? "Sent when idle" : at && !recurring ? `Sent ${dayClock(at, now)}` : "Sent";
  else if (idle) state = "When idle";
  else if (recurring) state = recurrenceSentence(message.cron ?? "", message.timezone, localZone);
  else state = at ? capitalize(dayClock(at, now)) : "Pending";
  const command = message.command ? `/${message.command.name}` : "";
  return {
    key: `message:${message.id}`,
    recurring,
    title: messageText(message.text || command, message.attachments?.length ?? 0),
    countdown: pending && !idle && at ? countdown(at, now) : null,
    state,
    failed: message.status === "failed",
    lastRun: lastRun(message, now),
    actions: pending ? ["sendNow", "cancel"] : message.status === "sent" ? ["open", "dismiss"] : ["dismiss"],
    resolved: !pending,
    target: sessionTitle || `session ${message.session_id.split("-").pop()}`,
    sessionId: message.session_id,
  };
}
