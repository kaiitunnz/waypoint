"use client";

import type { MouseEvent, ReactNode } from "react";

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
  // The transcript's own content when it is not a conversation, such as a placeholder.
  override?: ReactNode;
}

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
  override,
}: TranscriptProps) {
  const { scroller, content, log, away, onScroll, hold, jumpToEnd } = useTranscriptScroll(turns, pending.length);
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
          {override ?? (
            <TranscriptHoldProvider value={hold}>
              {hasOlder ? (
                <div className={styles.older}>
                  <ActionChip icon="chevron-up" onClick={onLoadOlder} aria-disabled={loadingOlder}>
                    {loadingOlder ? "Loading older messages…" : "Load older messages"}
                  </ActionChip>
                </div>
              ) : null}
              <div className={styles.messages}>
                <div ref={log} className={styles.turns} role="log" aria-label="Transcript">
                  {turns.map((turn, index) => (
                    <TurnView key={turn.key} turn={turn} working={working && index === turns.length - 1} />
                  ))}
                </div>
                {pending.length > 0 ? <PendingTurn messages={pending} /> : null}
                {!loading && turns.length === 0 && pending.length === 0 ? (
                  <EmptyTranscript status={status} hiddenCount={hiddenCount} onShowAll={onShowAll} />
                ) : null}
              </div>
            </TranscriptHoldProvider>
          )}
        </div>
      </div>
      {away && !override ? (
        <button type="button" className={styles.jump} onClick={jumpToEnd}>
          <Icon name="arrow-down" size={16} />
          Jump to latest
        </button>
      ) : null}
    </div>
  );
}
