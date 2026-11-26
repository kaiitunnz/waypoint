"use client";

import { useCallback, useRef, useState } from "react";

import { setTaskDockDismissed } from "../api";
import { dismissedTaskSequence, isTaskProgressDismissed, type PendingTaskDismissal } from "../todos";
import type { EventRecord, SessionRecord } from "../types";

interface TaskDismissalOptions {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  // The newest task snapshot.
  todo: EventRecord | null;
  onSession: (session: SessionRecord) => void;
  onError: (error: unknown, fallback: string) => void;
}

// Whether the human has put the current task list away. The choice lives on the
// session record, so it follows the session to other devices; a newer list shows
// the tasks again.
export function useTaskDismissal({ host, token, sessionId, session, todo, onSession, onError }: TaskDismissalOptions) {
  const [pending, setPending] = useState<PendingTaskDismissal | null>(null);
  const request = useRef(0);

  const update = useCallback(
    async (sequence: number | null) => {
      const id = ++request.current;
      setPending({ sessionId, sequence });
      try {
        const updated = await setTaskDockDismissed(host, token, sessionId, sequence);
        if (id === request.current) onSession(updated);
      } catch (error) {
        onError(error, "failed to update task progress");
      } finally {
        if (id === request.current) setPending(null);
      }
    },
    [host, token, sessionId, onSession, onError],
  );

  return {
    dismissed: isTaskProgressDismissed(todo, dismissedTaskSequence(pending, sessionId, session?.task_dock_dismissed_sequence)),
    dismiss: () => {
      if (todo) void update(todo.sequence);
    },
    show: () => void update(null),
  };
}
