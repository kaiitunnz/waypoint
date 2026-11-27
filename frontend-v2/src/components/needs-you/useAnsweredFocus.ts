"use client";

import { useLayoutEffect, useRef } from "react";

import type { QueueEntry } from "@waypoint/web-core/needsYou";

import type { AskLocks } from "@/components/sessions/AskLocks";

// An answered ask leaves the queue, taking with it whatever had the focus in its row or
// its detail. Beside the item, the row that takes its place gets the focus, else the
// queue does.
export function useAnsweredFocus(entries: readonly QueueEntry[], columns: boolean, locks: AskLocks | null) {
  const queueRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const shown = useRef<string[]>([]);

  useLayoutEffect(() => {
    const before = shown.current;
    const keys = entries.map((entry) => entry.key);
    shown.current = keys;
    const left = before.findIndex((key) => {
      const state = locks?.phase(key).state;
      return !keys.includes(key) && (state === "sending" || state === "sent");
    });
    // Focus the person has since put somewhere else stays there.
    if (!columns || left === -1 || (document.activeElement ?? document.body) !== document.body) return;
    const rows = listRef.current?.children;
    const next = rows?.[Math.min(left, rows.length - 1)]?.querySelector<HTMLElement>("a");
    (next ?? queueRef.current)?.focus();
  }, [entries, columns, locks]);

  return { queueRef, listRef };
}
