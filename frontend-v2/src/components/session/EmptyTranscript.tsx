import type { SessionStatus } from "@waypoint/web-core/types";

import { EmptyState } from "@/components/ui/EmptyState";

import styles from "./EmptyTranscript.module.css";

interface EmptyTranscriptProps {
  status: SessionStatus | null;
  // Events Summary left out.
  hiddenCount: number;
  onShowAll: () => void;
}

export function EmptyTranscript({ status, hiddenCount, onShowAll }: EmptyTranscriptProps) {
  if (hiddenCount > 0) {
    return (
      <div className={styles.empty}>
        <EmptyState title="Nothing important yet" detail={`${hiddenCount} low-signal event${hiddenCount === 1 ? "" : "s"} hidden`} />
        <button type="button" className={styles.showAll} onClick={onShowAll}>
          Show all events
        </button>
      </div>
    );
  }
  return status === "exited" ? (
    <EmptyState title="Session exited" detail="No transcript was captured." />
  ) : (
    <EmptyState title="Waiting for the agent…" />
  );
}
