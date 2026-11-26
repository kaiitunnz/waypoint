"use client";

import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";

import { userEchoes } from "../session/optimistic";
import { removeConfirmedOptimisticMessages } from "../session/transcript";
import type { AttachmentSpec, EventRecord } from "../types";

// A delivered message whose echo has not come by now is dropped.
const ECHO_TIMEOUT_MS = 10_000;

export interface PendingSend {
  id: string;
  text: string;
  ts: string;
  attachments: AttachmentSpec[];
}

export interface OptimisticSends {
  pending: PendingSend[];
  // Shows a message at once. Call the returned function when the request ends,
  // saying whether the transcript will echo it.
  add: (text: string, attachments?: AttachmentSpec[]) => (echoes: boolean) => void;
}

// Messages shown while they send: each leaves when the transcript echoes it.
// Pass the events the transcript renders, so none leaves before its echo shows.
export function useOptimisticSends(events: readonly EventRecord[]): OptimisticSends {
  const [pending, setPending] = useState<PendingSend[]>([]);
  const lastId = useRef<number | null>(null);
  const counter = useRef(0);
  const timeouts = useRef(new Set<number>());

  useEffect(() => {
    const pendingTimeouts = timeouts.current;
    return () => pendingTimeouts.forEach((timeout) => window.clearTimeout(timeout));
  }, []);

  // Before paint, so an echo and its message never show together.
  useLayoutEffect(() => {
    const echoes = userEchoes(events, lastId.current);
    lastId.current = echoes.lastId;
    if (echoes.texts.length > 0) {
      setPending((current) => removeConfirmedOptimisticMessages(current, echoes.texts));
    }
  }, [events]);

  const add = useCallback((text: string, attachments: AttachmentSpec[] = []) => {
    const id = `pending-${(counter.current += 1)}`;
    setPending((current) => [...current, { id, text, attachments, ts: new Date().toISOString() }]);
    const drop = () => setPending((current) => current.filter((item) => item.id !== id));
    return (echoes: boolean) => {
      if (echoes) timeouts.current.add(window.setTimeout(drop, ECHO_TIMEOUT_MS));
      else drop();
    };
  }, []);

  return { pending, add };
}
