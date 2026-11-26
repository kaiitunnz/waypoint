import { cronToLabel, isRecurring } from "../recurrence";
import type { HeldMessage, MessageSchedule } from "../types";
import { formatRelativeTime } from "../usage";
import { heldMessageOriginLabel, heldMessageText, holdReasonLabel } from "./heldMessages";
import { describeScheduledDelivery, pendingSchedulesSorted } from "./scheduledMessages";

// One queue for everything a session will receive later: messages it holds and
// messages scheduled for it.
export type QueueItem =
  | { kind: "held"; id: string; message: HeldMessage }
  | { kind: "scheduled"; id: string; schedule: MessageSchedule };

export type QueueAction = "release" | "sendNow" | "cancel";

// Held messages first, in the order they arrived, since they are waiting now;
// then pending schedules, soonest first.
export function buildMessageQueue(held: HeldMessage[], scheduled: MessageSchedule[]): QueueItem[] {
  return [
    ...held.map((message): QueueItem => ({ kind: "held", id: message.id, message })),
    ...pendingSchedulesSorted(scheduled).map((schedule): QueueItem => ({ kind: "scheduled", id: schedule.id, schedule })),
  ];
}

// The strip shows the latest held message, or the next scheduled one.
export function queueStripItem(queue: QueueItem[]): QueueItem | null {
  const held = queue.filter((item) => item.kind === "held");
  return held.at(-1) ?? queue[0] ?? null;
}

export function heldCount(queue: QueueItem[]): number {
  return queue.filter((item) => item.kind === "held").length;
}

export interface QueueItemView {
  origin: string;
  // A held message's age, or when a schedule delivers.
  when: string;
  // The repeat rule of a recurring schedule.
  cadence: string | null;
  // Why a held message has not gone out yet; a schedule's `when` says it.
  reason: string | null;
  // The hold is the human's (Focus) rather than the session's state.
  manual: boolean;
  text: string;
  actions: QueueAction[];
}

export interface QueueViewOptions {
  // Focus is on for the session.
  focus: boolean;
  // A schedule can be delivered on request.
  canSendNow?: boolean;
}

export function queueItemView(item: QueueItem, { focus, canSendNow = false }: QueueViewOptions): QueueItemView {
  if (item.kind === "held") {
    const { message } = item;
    const reason = holdReasonLabel(message, focus);
    return {
      origin: heldMessageOriginLabel(message),
      when: formatRelativeTime(message.created_at),
      cadence: null,
      reason: reason.text,
      manual: reason.manual,
      text: heldMessageText(message),
      actions: ["release", "cancel"],
    };
  }
  const { schedule } = item;
  const attached = schedule.attachments?.length ?? 0;
  const suffix = attached ? ` (+${attached} file${attached === 1 ? "" : "s"})` : "";
  return {
    origin: "Scheduled",
    when: describeScheduledDelivery(schedule),
    cadence: isRecurring(schedule) ? cronToLabel(schedule.cron, schedule.timezone) : null,
    reason: null,
    manual: false,
    text: (schedule.text || "(no text)") + suffix,
    actions: canSendNow ? ["sendNow", "cancel"] : ["cancel"],
  };
}

// A `held_messages` frame's list for this session; null for another session's frame.
export function parseHeldMessagesFrame(payload: unknown, sessionId: string): HeldMessage[] | null {
  if (!payload || typeof payload !== "object") return null;
  const { session_id, held_messages } = payload as { session_id?: unknown; held_messages?: unknown };
  if (session_id !== sessionId) return null;
  return Array.isArray(held_messages) ? (held_messages as HeldMessage[]) : [];
}
