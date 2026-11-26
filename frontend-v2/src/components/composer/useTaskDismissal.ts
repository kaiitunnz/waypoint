"use client";

import { useCallback, useRef, useState } from "react";

import { isAuthError, setTaskDockDismissed } from "@waypoint/web-core/api";
import {
  dismissedTaskSequence,
  isTaskProgressDismissed,
  type PendingTaskDismissal,
} from "@waypoint/web-core/todos";
import type { EventRecord, SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";

interface Options {
  host: string;
  token: string;
  sessionId: string;
  session: SessionRecord | null;
  // The newest task snapshot.
  todo: EventRecord | null;
  onSession: (session: SessionRecord) => void;
  onError: (message: string) => void;
}

// Whether the human has put the current task list away. The choice lives on the
// session record, so it follows the session to other devices; a newer list
// shows the tasks again.
export function useTaskDismissal({ host, token, sessionId, session, todo, onSession, onError }: Options) {
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
        if (isAuthError(error)) handleAuthFailure();
        else onError(error instanceof Error ? error.message : "failed to update tasks");
      } finally {
        if (id === request.current) setPending(null);
      }
    },
    [host, token, sessionId, onSession, onError],
  );

  const dismissed = isTaskProgressDismissed(todo, dismissedTaskSequence(pending, sessionId, session?.task_dock_dismissed_sequence));
  return {
    dismissed,
    dismiss: () => {
      if (todo) void update(todo.sequence);
    },
    show: () => void update(null),
  };
}
