import { CWD_NOT_FOUND_DETAIL, SSH_MASTER_REQUIRED_DETAIL } from "./api";
import { humaniseBackend, permissionModeLabel, type BackendCatalog } from "./backends";
import { isIdle, isRecurring, recurrenceSentence } from "./recurrence";
import { countdown, dayClock } from "./scheduleTime";
import { messageText } from "./session/heldMessages";
import type { MessageSchedule, ScheduledSession } from "./types";
import { splitPath } from "./workspacePaths";

const capitalize = (text: string) => text.charAt(0).toUpperCase() + text.slice(1);

// The server records an HTTP refusal as "400: <detail>".
export function failureText(reason: string | null | undefined): string {
  const detail = (reason ?? "").replace(/^\d{3}: /, "");
  if (detail === CWD_NOT_FOUND_DETAIL) return "folder not found";
  if (detail === SSH_MASTER_REQUIRED_DETAIL) return "SSH password required";
  return detail || "unknown error";
}

export type ScheduledAction = "open" | "openLast" | "sendNow" | "cancel" | "dismiss";

export interface ScheduledRowView {
  id: string;
  recurring: boolean;
  title: string;
  countdown: string | null;
  // When it runs or what happened.
  state: string;
  failed: boolean;
  lastRun: { text: string; failed: boolean } | null;
  actions: ScheduledAction[];
  resolved: boolean;
  // The session to open: a message's target, or the one a launch started.
  sessionId: string | null;
}

export interface ScheduledLaunchView extends ScheduledRowView {
  kind: "launch";
  // Agent, model and permission mode.
  agent: string;
  folder: string;
}

export interface ScheduledMessageView extends ScheduledRowView {
  kind: "message";
  // The target session's title, or null when it isn't known.
  target: string | null;
  sessionId: string;
}

interface Sortable {
  status: string;
  created_at: string;
  scheduled_at?: string | null;
  last_run_at?: string | null;
  trigger?: MessageSchedule["trigger"];
}

const planned = (item: Sortable) => (item.scheduled_at ? Date.parse(item.scheduled_at) : 0);

// When a finished record last ran. A cancelled one may never have, and its
// planned time can be far ahead, so it counts from when it was made.
function settledAt(item: Sortable): number {
  if (item.last_run_at) return Date.parse(item.last_run_at);
  return item.status === "cancelled" ? Date.parse(item.created_at) : planned(item);
}

// Pending first, soonest first with when-idle last; then the rest, newest first.
export function sortScheduled<T extends Sortable>(items: T[]): T[] {
  const pending = items.filter((item) => item.status === "pending");
  const rest = items.filter((item) => item.status !== "pending");
  pending.sort((a, b) => Number(isIdle(a)) - Number(isIdle(b)) || planned(a) - planned(b));
  rest.sort((a, b) => settledAt(b) - settledAt(a));
  return [...pending, ...rest];
}

type RunRecord = Pick<MessageSchedule, "cron" | "last_run_at" | "last_run_status" | "last_failure_reason">;

function lastRun(record: RunRecord, now: Date) {
  if (!isRecurring(record) || !record.last_run_at) return null;
  if (record.last_run_status === "failed") {
    return { text: `Last run failed: ${failureText(record.last_failure_reason)}`, failed: true };
  }
  return { text: `Last run ${dayClock(new Date(record.last_run_at), now)}, ${record.last_run_status ?? "done"}`, failed: false };
}

const folderName = (cwd: string) => splitPath(cwd.replace(/\/+$/, "")).base || cwd;

export function launchRowView(
  schedule: ScheduledSession,
  catalog: BackendCatalog,
  now: Date,
  localZone: string,
  model: string | null,
): ScheduledLaunchView {
  const recurring = isRecurring(schedule);
  const at = new Date(schedule.scheduled_at);
  const pending = schedule.status === "pending";
  let state: string;
  if (schedule.status === "failed") state = `Failed: ${failureText(schedule.failure_reason)}`;
  else if (schedule.status === "cancelled") state = "Cancelled";
  else if (schedule.status === "launched") state = `Launched ${dayClock(at, now)}`;
  else if (schedule.cron) state = recurrenceSentence(schedule.cron, schedule.timezone, localZone);
  else state = capitalize(dayClock(at, now));
  const agent = [
    humaniseBackend(schedule.backend, catalog),
    model,
    schedule.permission_mode ? (permissionModeLabel(schedule.backend, schedule.permission_mode, catalog) ?? schedule.permission_mode) : null,
  ]
    .filter(Boolean)
    .join(", ");
  let actions: ScheduledAction[];
  if (pending) actions = recurring && schedule.session_id ? ["openLast", "cancel"] : ["cancel"];
  else if (schedule.status === "launched" && schedule.session_id) actions = ["open", "dismiss"];
  else actions = ["dismiss"];
  return {
    id: schedule.id,
    kind: "launch",
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
  const recurring = isRecurring(message);
  const idle = isIdle(message);
  const pending = message.status === "pending";
  const at = message.scheduled_at ? new Date(message.scheduled_at) : null;
  // An idle message's time is when it was queued until a delivery replaces it.
  const idleRanAt = at && message.scheduled_at !== message.created_at ? at : null;
  let state: string;
  if (message.status === "failed") state = `Failed: ${failureText(message.failure_reason)}`;
  else if (message.status === "cancelled") state = "Cancelled";
  else if (message.status === "sent" && idle) state = idleRanAt ? `Sent when idle, ${dayClock(idleRanAt, now)}` : "Sent when idle";
  else if (message.status === "sent") state = at ? `Sent ${dayClock(at, now)}` : "Sent";
  else if (idle) state = "When idle";
  else if (message.cron) state = recurrenceSentence(message.cron, message.timezone, localZone);
  else state = at ? capitalize(dayClock(at, now)) : "Pending";
  const command = message.command ? `/${message.command.name}` : "";
  return {
    id: message.id,
    kind: "message",
    recurring,
    title: messageText(message.text || command, message.attachments?.length ?? 0),
    countdown: pending && !idle && at ? countdown(at, now) : null,
    state,
    failed: message.status === "failed",
    lastRun: lastRun(message, now),
    actions: pending ? ["sendNow", "cancel"] : message.status === "sent" ? ["open", "dismiss"] : ["dismiss"],
    resolved: !pending,
    target: sessionTitle || null,
    sessionId: message.session_id,
  };
}
