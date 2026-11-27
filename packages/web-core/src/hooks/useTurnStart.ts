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
  onAuthFailure?: () => void;
}

// When the running turn started, as epoch ms, or null while that is unknown. The
// transcript holds a window of messages, so a start older than it is paged to once.
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
  const beyond = running && hasOlder && !loaded.bounded;
  const [found, setFound] = useState<{ sessionId: string; startedAt: number | null } | null>(null);
  // A search is for one turn.
  if (!beyond && found !== null) setFound(null);

  // The search reads what is loaded when it starts.
  const latest = useRef({ events, oldestSequence, onAuthFailure });
  useEffect(() => {
    latest.current = { events, oldestSequence, onAuthFailure };
  });

  useEffect(() => {
    const { events: held, oldestSequence: before } = latest.current;
    if (!beyond || before === null) return;
    let active = true;
    findTurnStart(held, before, (beforeSequence) => fetchEvents(host, token, sessionId, { beforeSequence })).then(
      (startedAt) => {
        if (active) setFound({ sessionId, startedAt });
      },
      // The row goes without a time.
      (error) => {
        if (active && isAuthError(error)) latest.current.onAuthFailure?.();
      },
    );
    return () => {
      active = false;
    };
  }, [beyond, host, token, sessionId]);

  if (!beyond) return loaded.startedAt;
  return found?.sessionId === sessionId ? found.startedAt : null;
}
