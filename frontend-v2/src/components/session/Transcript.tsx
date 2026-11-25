"use client";

import type { ReactNode } from "react";

import { isWorkingStatus } from "@waypoint/web-core/sessionPresentation";
import type { TranscriptTurn } from "@waypoint/web-core/session/turns";
import type { SessionStatus } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { EmptyTranscript } from "./EmptyTranscript";
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
  override,
}: TranscriptProps) {
  const { scroller, content, away, onScroll, loadOlder, jumpToEnd } = useTranscriptScroll(turns, loadingOlder, onLoadOlder);
  const working = isWorkingStatus(status);

  return (
    <div className={styles.frame}>
      <div ref={scroller} className={styles.scroller} onScroll={onScroll}>
        <div ref={content} className={cx(styles.col, Boolean(override) && styles.top)}>
          {override ?? (
            <>
              {hasOlder ? (
                <div className={styles.older}>
                  <ActionChip icon="chevron-up" onClick={loadOlder} disabled={loadingOlder}>
                    {loadingOlder ? "Loading older messages…" : "Load older messages"}
                  </ActionChip>
                </div>
              ) : null}
              <div className={styles.turns} role="log" aria-label="Transcript">
                {turns.map((turn, index) => (
                  <TurnView key={turn.key} turn={turn} working={working && index === turns.length - 1} />
                ))}
              </div>
              {!loading && turns.length === 0 ? (
                <EmptyTranscript status={status} hiddenCount={hiddenCount} onShowAll={onShowAll} />
              ) : null}
            </>
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
