"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { userEchoes } from "../session/optimistic";
import { removeConfirmedOptimisticMessages } from "../session/transcript";
import type { AttachmentSpec, EventRecord } from "../types";

export interface PendingSend {
  id: string;
  text: string;
  ts: string;
  attachments: AttachmentSpec[];
}

export interface OptimisticSends {
  pending: PendingSend[];
  // Shows a message at once. Call the returned function when the request ends.
  add: (text: string, attachments?: AttachmentSpec[]) => () => void;
}

// Messages shown while they send: each leaves when the transcript echoes it, or
// when its request ends.
export function useOptimisticSends(events: readonly EventRecord[]): OptimisticSends {
  const [pending, setPending] = useState<PendingSend[]>([]);
  const lastId = useRef<number | null>(null);
  const counter = useRef(0);

  useEffect(() => {
    const echoes = userEchoes(events, lastId.current);
    lastId.current = echoes.lastId;
    if (echoes.texts.length > 0) {
      setPending((current) => removeConfirmedOptimisticMessages(current, echoes.texts));
    }
  }, [events]);

  const add = useCallback((text: string, attachments: AttachmentSpec[] = []) => {
    const id = `pending-${(counter.current += 1)}`;
    setPending((current) => [...current, { id, text, attachments, ts: new Date().toISOString() }]);
    return () => setPending((current) => current.filter((item) => item.id !== id));
  }, []);

  return { pending, add };
}
