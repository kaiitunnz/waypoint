import type { SessionStatus } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { EmptyState } from "@/components/ui/EmptyState";

interface EmptyTranscriptProps {
  status: SessionStatus | null;
  // Events Summary left out.
  hiddenCount: number;
  onShowAll: () => void;
}

export function EmptyTranscript({ status, hiddenCount, onShowAll }: EmptyTranscriptProps) {
  if (hiddenCount > 0) {
    return (
      <EmptyState
        title="Nothing important yet"
        detail={`${hiddenCount} low-signal event${hiddenCount === 1 ? "" : "s"} hidden`}
        action={<ActionChip onClick={onShowAll}>Show all events</ActionChip>}
      />
    );
  }
  return status === "exited" ? (
    <EmptyState title="Session exited" detail="No transcript was captured." />
  ) : (
    <EmptyState title="Waiting for the agent…" />
  );
}
