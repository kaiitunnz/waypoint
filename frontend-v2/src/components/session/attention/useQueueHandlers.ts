"use client";

import { cancelHeldMessages, releaseHeldMessages } from "@waypoint/web-core/api";
import { sendNowOutcome, type QueueAction, type QueueItem } from "@waypoint/web-core/session/messageQueue";
import type { MessageSchedule } from "@waypoint/web-core/types";

import { useToast } from "@/components/ui/Toast";
import { useFailureToast } from "@/lib/useFailureToast";

import { useSessionContext } from "../SessionContext";
import type { QueueHandlers } from "./QueueList";

export interface ScheduledMessages {
  messages: MessageSchedule[];
  cancel: (id: string) => Promise<void>;
  // Resolves to whether the session held the message.
  sendNow: (id: string) => Promise<boolean>;
}

// What the message queue does with a held or scheduled message, for every place that lists it.
export function useQueueHandlers(scheduled: ScheduledMessages): QueueHandlers {
  const { host, token, sessionId } = useSessionContext();
  const report = useFailureToast();
  const { toast } = useToast();

  const run = async (request: () => Promise<void>, fallback: string) => {
    try {
      await request();
    } catch (error) {
      report(error, fallback);
    }
  };
  const onAction = async (item: QueueItem, action: QueueAction) => {
    if (item.kind === "scheduled") {
      if (action === "cancel") await run(() => scheduled.cancel(item.id), "Could not cancel the message");
      if (action === "sendNow") {
        await run(async () => toast({ message: sendNowOutcome(await scheduled.sendNow(item.id)) }), "Could not send the message");
      }
      return;
    }
    const target = { heldId: item.id };
    if (action === "release") await run(() => releaseHeldMessages(host, token, target), "Could not release the message");
    if (action === "cancel") await run(() => cancelHeldMessages(host, token, target), "Could not cancel the message");
  };
  return {
    onAction,
    onReleaseAll: () => run(() => releaseHeldMessages(host, token, { sessionId }), "Could not release the messages"),
    onCancelAll: () => run(() => cancelHeldMessages(host, token, { sessionId }), "Could not cancel the messages"),
  };
}
