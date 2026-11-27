"use client";

import { useEffect, useMemo, useRef, useState } from "react";

import { fetchEvents, isAuthError } from "../api";
import { findTurnStart, turnStart, type FetchOlderEvents } from "../session/workingRow";
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

interface SearchOutcome {
  onFound: (startedAt: number | null) => void;
  onAuthFailure: () => void;
}

// Searches older pages for a turn's start. The returned function drops the
// search: it reads no further page and reports nothing.
export function searchTurnStart(
  events: EventRecord[],
  oldestSequence: number,
  fetchOlder: FetchOlderEvents,
  { onFound, onAuthFailure }: SearchOutcome,
): () => void {
  let dropped = false;
  findTurnStart(events, oldestSequence, (before) => (dropped ? Promise.reject(new Error("dropped")) : fetchOlder(before))).then(
    (startedAt) => {
      if (!dropped) onFound(startedAt);
    },
    // Any other failure leaves the row without a time.
    (error) => {
      if (!dropped && isAuthError(error)) onAuthFailure();
    },
  );
  return () => {
    dropped = true;
  };
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
    return searchTurnStart(held, before, (beforeSequence) => fetchEvents(host, token, sessionId, { beforeSequence }), {
      onFound: (startedAt) => setFound({ sessionId, startedAt }),
      onAuthFailure: () => latest.current.onAuthFailure?.(),
    });
  }, [beyond, host, token, sessionId]);

  if (!beyond) return loaded.startedAt;
  return found?.sessionId === sessionId ? found.startedAt : null;
}
