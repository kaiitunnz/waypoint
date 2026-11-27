"use client";

import { useNow } from "@waypoint/web-core/hooks/useNow";
import { formatElapsed } from "@waypoint/web-core/session/workingRow";

import { StatusLamp } from "@/components/ui/StatusLamp";

import styles from "./WorkingRow.module.css";

interface WorkingRowProps {
  step: string | null;
  // Epoch ms; null leaves the time out.
  startedAt: number | null;
}

function Elapsed({ since }: { since: number }) {
  const now = useNow(1000);
  return (
    <>
      <span className={styles.sep} aria-hidden="true">
        ·
      </span>
      <span className={styles.elapsed}>{formatElapsed(now - since)}</span>
    </>
  );
}

export function WorkingRow({ step, startedAt }: WorkingRowProps) {
  return (
    <div className={styles.row}>
      <span className={styles.lamp}>
        <StatusLamp state="working" />
      </span>
      {/* The ticking time sits outside the live region, which would announce every second. */}
      <span role="status" className={styles.status}>
        <span className={styles.word}>Working</span>
        {step ? (
          <>
            <span className={styles.sep} aria-hidden="true">
              ·
            </span>
            <span className={styles.step}>{step}</span>
          </>
        ) : null}
      </span>
      {startedAt === null ? null : <Elapsed since={startedAt} />}
    </div>
  );
}
