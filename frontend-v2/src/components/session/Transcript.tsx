"use client";

import { useEffect, type MouseEvent, type ReactNode } from "react";

import { isWorkingStatus } from "@waypoint/web-core/sessionPresentation";
import type { PendingSend } from "@waypoint/web-core/hooks/useOptimisticSends";
import type { TranscriptTurn } from "@waypoint/web-core/session/turns";
import type { SessionStatus } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";

import { EmptyTranscript } from "./EmptyTranscript";
import { PendingTurn } from "./PendingTurn";
import { TranscriptHoldProvider } from "./SessionContext";
import styles from "./Transcript.module.css";
import { TurnView } from "./TurnView";
import { useTranscriptScroll } from "./useTranscriptScroll";

interface TranscriptProps {
  turns: TranscriptTurn[];
  status: SessionStatus | null;
  loading: boolean;
  hasOlder: boolean;
  loadingOlder: boolean;
  onLoadOlder: () => void;
  hiddenCount: number;
  onShowAll: () => void;
  pending: PendingSend[];
  // The running turn's row, at the transcript's end.
  workingRow?: ReactNode;
  // A question card to scroll to and mark; `seq` makes a repeat a new request.
  reveal?: { toolUseId: string; seq: number } | null;
  // Whether the card was on the page.
  onRevealed?: (found: boolean) => void;
}

// Frames to wait for a card that is still rendering.
const REVEAL_FRAMES = 60;
const FLASH_MS = 2000;

export function Transcript({
  turns,
  status,
  loading,
  hasOlder,
  loadingOlder,
  onLoadOlder,
  hiddenCount,
  onShowAll,
  pending,
  workingRow,
  reveal,
  onRevealed,
}: TranscriptProps) {
  const { scroller, content, log, away, onScroll, hold, jumpToEnd, reveal: bringIntoView } = useTranscriptScroll(turns, pending.length);

  useEffect(() => {
    if (!reveal) return;
    let frame = 0;
    let attempts = 0;
    const find = () => {
      const card = scroller.current?.querySelector<HTMLElement>(`[data-ask-tool-use-id="${CSS.escape(reveal.toolUseId)}"]`);
      if (card) {
        bringIntoView(card);
        card.removeAttribute("data-flash");
        // A reflow restarts the animation for a card already marked.
        void card.offsetWidth;
        card.setAttribute("data-flash", "");
        setTimeout(() => card.removeAttribute("data-flash"), FLASH_MS);
        onRevealed?.(true);
        return;
      }
      attempts += 1;
      if (attempts > REVEAL_FRAMES) onRevealed?.(false);
      else frame = requestAnimationFrame(find);
    };
    frame = requestAnimationFrame(find);
    return () => cancelAnimationFrame(frame);
  }, [reveal, scroller, bringIntoView, onRevealed]);
  const working = isWorkingStatus(status);
  // Every fold control carries aria-expanded; captured here, before the fold changes.
  const onFold = (event: MouseEvent) => {
    const toggle = (event.target as Element).closest("[aria-expanded]");
    if (toggle) hold(toggle, toggle.getAttribute("aria-expanded") === "true");
  };

  return (
    <div className={styles.frame}>
      <div ref={scroller} className={styles.scroller} onScroll={onScroll} onClickCapture={onFold}>
        <div ref={content} className={styles.col}>
          <TranscriptHoldProvider value={hold}>
            <div className={styles.messages}>
              {hasOlder ? (
                <div className={styles.older}>
                  <ActionChip icon="chevron-up" onClick={onLoadOlder} aria-disabled={loadingOlder}>
                    {loadingOlder ? "Loading older messages…" : "Load older messages"}
                  </ActionChip>
                </div>
              ) : null}
              <div ref={log} className={styles.turns} role="log" aria-label="Transcript">
                {turns.map((turn, index) => (
                  <TurnView key={turn.key} turn={turn} working={working && index === turns.length - 1} />
                ))}
              </div>
              {pending.length > 0 ? <PendingTurn messages={pending} /> : null}
              {workingRow}
              {/* The working row already says the agent is at work. */}
              {!loading && turns.length === 0 && pending.length === 0 && (hiddenCount > 0 || !workingRow) ? (
                <EmptyTranscript status={status} hiddenCount={hiddenCount} onShowAll={onShowAll} />
              ) : null}
            </div>
          </TranscriptHoldProvider>
        </div>
      </div>
      {away ? (
        <button type="button" className={styles.jump} onClick={jumpToEnd}>
          <Icon name="arrow-down" size={16} />
          Jump to latest
        </button>
      ) : null}
    </div>
  );
}
