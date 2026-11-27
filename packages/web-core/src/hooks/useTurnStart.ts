"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { fetchEvents, isAuthError } from "../api";
import { findTurnStart, turnStart } from "../session/workingRow";
import type { EventRecord } from "../types";

interface TurnStartInput {
  host: string;
  token: string;
  sessionId: string;
  events: EventRecord[];
  // The cursor older pages are read from.
  oldestSequence: number | null;
  hasOlder: boolean;
  running: boolean;
  onAuthFailure: () => void;
}

interface LoadedTurn {
  hasOlder: boolean;
  // The end of the turn before is loaded.
  bounded: boolean;
  running: boolean;
}

// What to do about a turn that began before the loaded events, given whether a
// search has answered. Only a loaded end of the turn before drops the answer, so
// a pause such as an approval prompt or a reconnect keeps it, and a search that
// failed runs again when the turn resumes.
export function planTurnSearch({ hasOlder, bounded, running }: LoadedTurn, answered: boolean) {
  const outside = hasOlder && !bounded;
  return { drop: !outside && answered, search: outside && running && !answered };
}

// When the running turn started, as epoch ms, or null while that is unknown. The
// transcript holds a window of messages, so an older start is searched for.
export function useTurnStart({
  host,
  token,
  sessionId,
  events,
  oldestSequence,
  hasOlder,
  running,
  onAuthFailure,
}: TurnStartInput): number | null {
  const loaded = useMemo(() => turnStart(events), [events]);
  // A search's answer; its start is null where that is out of reach.
  const [answer, setAnswer] = useState<{ startedAt: number | null } | null>(null);
  const { drop, search } = planTurnSearch({ hasOlder, bounded: loaded.bounded, running }, answer !== null);
  if (drop) setAnswer(null);

  // The search reads what is loaded when it starts.
  const latest = useRef({ events, oldestSequence });
  useEffect(() => {
    latest.current = { events, oldestSequence };
  });

  useEffect(() => {
    const { events: held, oldestSequence: before } = latest.current;
    if (!search || before === null) return;
    const controller = new AbortController();
    const { signal } = controller;
    findTurnStart(held, before, (beforeSequence) => fetchEvents(host, token, sessionId, { beforeSequence, signal })).then(
      (startedAt) => {
        if (!signal.aborted) setAnswer({ startedAt });
      },
      // Any other failure leaves the row without a time.
      (error) => {
        if (!signal.aborted && isAuthError(error)) onAuthFailure();
      },
    );
    return () => controller.abort();
  }, [search, host, token, sessionId, onAuthFailure]);

  return hasOlder && !loaded.bounded ? (answer?.startedAt ?? null) : loaded.startedAt;
}
