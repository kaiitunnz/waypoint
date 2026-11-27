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

// Whether the turn's start is sought in older pages. A pause, such as an approval
// prompt or a reconnect, does not end the search; loading the end of the turn
// before does.
export function seeksTurnStart(seeking: boolean, { hasOlder, bounded, running }: LoadedTurn): boolean {
  return hasOlder && !bounded && (seeking || running);
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
  const [seeking, setSeeking] = useState(false);
  const [found, setFound] = useState<number | null>(null);
  const seek = seeksTurnStart(seeking, { hasOlder, bounded: loaded.bounded, running });
  if (seek !== seeking) {
    setSeeking(seek);
    // What was found belongs to the turn that was sought.
    if (!seek) setFound(null);
  }

  // The search reads what is loaded when it starts.
  const latest = useRef({ events, oldestSequence });
  useEffect(() => {
    latest.current = { events, oldestSequence };
  });

  useEffect(() => {
    const { events: held, oldestSequence: before } = latest.current;
    if (!seek || before === null) return;
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
  }, [seek, host, token, sessionId, onAuthFailure]);

  return hasOlder && !loaded.bounded ? found : loaded.startedAt;
}
