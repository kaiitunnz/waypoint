"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { fetchSessionsByIds } from "../api";
import type { MessageSchedule, SessionRecord } from "../types";

// `sessions` plus any other session a scheduled message targets, such as an
// exited one.
export function useScheduleTargetSessions(
  host: string,
  token: string,
  sessions: SessionRecord[],
  messages: MessageSchedule[],
): SessionRecord[] {
  const [fetched, setFetched] = useState<{ host: string; sessions: SessionRecord[] }>({ host, sessions: [] });
  const requested = useRef({ host, ids: new Set<string>() });

  useEffect(() => {
    if (!host || !token) return;
    if (requested.current.host !== host) requested.current = { host, ids: new Set() };
    const { ids } = requested.current;
    const known = new Set(sessions.map((session) => session.id));
    const missing = [...new Set(messages.map((message) => message.session_id))].filter((id) => !known.has(id) && !ids.has(id));
    if (!missing.length) return;
    // Remembered even on failure or when not found (deleted sessions), so a
    // frequently changing live list never re-requests the same ids.
    for (const id of missing) ids.add(id);
    fetchSessionsByIds(host, token, missing)
      .then((found) =>
        setFetched((current) => ({ host, sessions: [...(current.host === host ? current.sessions : []), ...found] })),
      )
      .catch(() => {});
  }, [host, token, sessions, messages]);

  return useMemo(() => {
    if (fetched.host !== host) return sessions;
    const live = new Set(sessions.map((session) => session.id));
    return [...sessions, ...fetched.sessions.filter((session) => !live.has(session.id))];
  }, [host, sessions, fetched]);
}
