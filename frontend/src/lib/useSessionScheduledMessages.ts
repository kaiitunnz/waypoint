"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteMessageSchedule, fetchMessageSchedules } from "@/lib/api";
import { MessageSchedule } from "@/lib/types";

// Live view of the *pending* scheduled messages for a single session, for the
// in-session dock. The global schedule_list_update broadcast only reaches the
// /ws/sessions socket (published with session_id=None), which the session page
// doesn't hold — so we fetch on mount, poll on a slow cadence, apply an
// optimistic cancel, and refresh after a create to keep the dock responsive.
export function useSessionScheduledMessages(
  host: string,
  token: string,
  sessionId: string,
) {
  const [messages, setMessages] = useState<MessageSchedule[]>([]);

  const refresh = useCallback(async () => {
    if (!host || !token || !sessionId) {
      return;
    }
    try {
      const all = await fetchMessageSchedules(host, token, sessionId);
      const pending = all
        .filter((m) => m.status === "pending")
        // Soonest first, undated last, so the dock's messages[0] is a robust
        // "next" regardless of backend ordering.
        .sort((a, b) => scheduledAtMs(a) - scheduledAtMs(b));
      // Keep the previous reference when nothing changed so the polling effect
      // doesn't tear down its interval and no needless re-render fires.
      setMessages((current) => (sameSchedules(current, pending) ? current : pending));
    } catch {
      // Transient fetch failures leave the last-known list in place.
    }
  }, [host, token, sessionId]);

  // Fetch once on mount / when the target changes.
  useEffect(() => {
    void refresh();
  }, [refresh]);

  // Adaptive polling: a message fires server-side and leaves the pending list,
  // but the session page never receives the schedule broadcast, so we poll to
  // notice. Poll fast when the soonest delivery is imminent or already past
  // (so a just-fired message clears within a few seconds instead of lingering
  // on "any moment"), and slowly otherwise.
  useEffect(() => {
    const soonest = messages.reduce((min, m) => {
      if (!m.scheduled_at) {
        return min;
      }
      return Math.min(min, new Date(m.scheduled_at).getTime());
    }, Number.POSITIVE_INFINITY);
    const dueSoon = soonest - Date.now() <= 30_000;
    const interval = dueSoon ? 4_000 : 15_000;
    const id = window.setInterval(() => void refresh(), interval);
    return () => window.clearInterval(id);
  }, [refresh, messages]);

  const cancel = useCallback(
    async (scheduleId: string) => {
      setMessages((current) => current.filter((m) => m.id !== scheduleId));
      try {
        await deleteMessageSchedule(host, token, scheduleId);
      } catch {
        // Re-sync from the server if the cancel didn't land.
        void refresh();
      }
    },
    [host, token, refresh],
  );

  return { messages, refresh, cancel };
}

function scheduledAtMs(m: MessageSchedule): number {
  return m.scheduled_at
    ? new Date(m.scheduled_at).getTime()
    : Number.POSITIVE_INFINITY;
}

function sameSchedules(a: MessageSchedule[], b: MessageSchedule[]): boolean {
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
