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
  const beyond = running && hasOlder && !loaded.bounded;
  const [found, setFound] = useState<number | null>(null);
  // A search is for one turn.
  if (!beyond && found !== null) setFound(null);

  // The search reads what is loaded when it starts.
  const latest = useRef({ events, oldestSequence });
  useEffect(() => {
    latest.current = { events, oldestSequence };
  });

  useEffect(() => {
    const { events: held, oldestSequence: before } = latest.current;
    if (!beyond || before === null) return;
    const search = new AbortController();
    const { signal } = search;
    findTurnStart(held, before, (beforeSequence) => fetchEvents(host, token, sessionId, { beforeSequence, signal })).then(
      (startedAt) => {
        if (!signal.aborted) setFound(startedAt);
      },
      // Any other failure leaves the row without a time.
      (error) => {
        if (!signal.aborted && isAuthError(error)) onAuthFailure();
      },
    );
    return () => search.abort();
  }, [beyond, host, token, sessionId, onAuthFailure]);

  return beyond ? found : loaded.startedAt;
}
