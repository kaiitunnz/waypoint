"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { TranscriptDensity } from "@waypoint/web-core/session/transcriptView";
import type { EventRecord } from "@waypoint/web-core/types";

// How many older pages Show in transcript loads looking for a question.
const PAGE_LIMIT = 50;

interface Options {
  events: EventRecord[];
  // The events the transcript has rendered, which trail `events`.
  rendered: EventRecord[];
  hasOlder: boolean;
  loadingOlder: boolean;
  loadOlder: () => void;
  density: TranscriptDensity;
  setDensity: (density: TranscriptDensity) => void;
  // Shows the conversation.
  showChat: () => void;
}

// Show in transcript: pages older events until the question's card is loaded, then
// hands the transcript one request to scroll to it.
export function useQuestionReveal({ events, rendered, hasOlder, loadingOlder, loadOlder, density, setDensity, showChat }: Options) {
  const [revealing, setRevealing] = useState<{ toolUseId: string; seq: number } | null>(null);
  const pagesLeft = useRef(0);

  const revealQuestion = useCallback(
    (toolUseId: string) => {
      showChat();
      pagesLeft.current = PAGE_LIMIT;
      setRevealing((current) => ({ toolUseId, seq: (current?.seq ?? 0) + 1 }));
    },
    [showChat],
  );

  const loaded =
    revealing !== null &&
    events.some((event) => event.kind === "tool_call" && event.metadata?.tool_use_id === revealing.toolUseId);
  useEffect(() => {
    if (!revealing || loaded || loadingOlder || !hasOlder || pagesLeft.current <= 0) return;
    pagesLeft.current -= 1;
    loadOlder();
  }, [revealing, loaded, loadingOlder, hasOlder, loadOlder]);

  const onRevealed = useCallback(
    (found: boolean) => {
      // Summary may leave the card out; Full shows every event.
      if (!found && density === "summary") {
        setDensity("full");
        setRevealing((current) => (current ? { ...current, seq: current.seq + 1 } : null));
        return;
      }
      setRevealing(null);
    },
    [density, setDensity],
  );

  // The card can be on the page only once the rendered events have caught up.
  const ready = loaded && rendered === events;
  // One object per request, so an unrelated render does not restart the transcript's search.
  const reveal = useMemo(() => (ready ? revealing : null), [ready, revealing]);
  return { reveal, onRevealed, revealQuestion };
}
