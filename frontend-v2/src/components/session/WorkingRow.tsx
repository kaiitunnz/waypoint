"use client";

import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
import { formatElapsed } from "@waypoint/web-core/session/workingRow";

import { StatusLamp } from "@/components/ui/StatusLamp";

import styles from "./WorkingRow.module.css";

export interface WorkingTurn {
  step: string | null;
  // Epoch ms; null leaves the time out.
  startedAt: number | null;
}

function Elapsed({ since }: { since: number }) {
  const now = useServerNow(1000);
  return (
    <>
      <span className={styles.sep} aria-hidden="true">
        ·
      </span>
      <span className={styles.elapsed}>{formatElapsed(now - since)}</span>
    </>
  );
}

export function WorkingRow({ turn }: { turn: WorkingTurn | null }) {
  return (
    <div className={turn ? styles.row : undefined}>
      {turn ? (
        <span className={styles.lamp}>
          <StatusLamp state="working" />
        </span>
      ) : null}
      {/* A live region is announced only if it is there before it fills. The ticking
          time sits outside it, which would otherwise announce every second. */}
      <span role="status" className={styles.status}>
        {turn ? (
          <>
            <span className={styles.word}>Working</span>
            {turn.step ? (
              <>
                <span className={styles.sep} aria-hidden="true">
                  ·
                </span>
                <span className={styles.step}>{turn.step}</span>
              </>
            ) : null}
          </>
        ) : null}
      </span>
      {turn && turn.startedAt !== null ? <Elapsed since={turn.startedAt} /> : null}
    </div>
  );
}
