"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteMessageSchedule, fetchMessageSchedules } from "../api";
import {
  pendingSchedulesSorted,
  sameSchedules,
  schedulePollIntervalMs,
} from "../session/scheduledMessages";
import { MessageSchedule } from "../types";

// Live view of the *pending* scheduled messages for a single session. The
// schedule broadcast goes only to /ws/sessions (session_id=None), which this
// hook doesn't subscribe to, so it fetches on mount, polls, and cancels
// optimistically; callers call `refresh` after creating one.
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
      const pending = pendingSchedulesSorted(all);
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
  // so poll to notice.
  useEffect(() => {
    const interval = schedulePollIntervalMs(messages);
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
