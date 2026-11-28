"use client";

import { useMemo } from "react";

import { buildMessageQueue } from "@waypoint/web-core/session/messageQueue";
import type { HeldMessage } from "@waypoint/web-core/types";

import { QueueList } from "@/components/session/attention/QueueList";
import { useQueueHandlers, type ScheduledMessages } from "@/components/session/attention/useQueueHandlers";

import { Section } from "./Section";

interface QueueSectionProps {
  heldMessages: HeldMessage[];
  scheduled: ScheduledMessages;
  focus: boolean;
}

// What the session will receive later: held and scheduled messages, with the dock's actions.
export function QueueSection({ heldMessages, scheduled, focus }: QueueSectionProps) {
  const handlers = useQueueHandlers(scheduled);
  const items = useMemo(() => buildMessageQueue(heldMessages, scheduled.messages), [heldMessages, scheduled.messages]);
  if (items.length === 0) return null;
  return (
    <Section title="Queue" count={String(items.length)}>
      <QueueList items={items} focus={focus} {...handlers} />
    </Section>
  );
}
