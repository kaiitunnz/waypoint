import type { PendingAsk } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";

import type { AskPhase } from "./useAskResponder";
import styles from "./AskStatus.module.css";

interface AskStatusProps {
  availability: PendingAsk["availability"];
  phase: AskPhase;
  // For a failure that does not carry its own retry.
  onRetry?: () => void;
}

export function AskStatus({ availability, phase, onRetry }: AskStatusProps) {
  if (phase.state === "error") {
    const retry = phase.retry ?? onRetry;
    return (
      <div className={styles.error} role="alert">
        <p>{phase.message}</p>
        {retry ? (
          <ActionChip icon="refresh" onClick={retry}>
            Retry
          </ActionChip>
        ) : null}
      </div>
    );
  }
  if (phase.state === "sending") {
    return <p className={styles.note}>{phase.retrying ? "Waiting for the agent…" : "Sending…"}</p>;
  }
  if (phase.state === "sent") return <p className={styles.note}>Sent</p>;
  if (availability === "starting") {
    return <p className={styles.note}>Waiting for the agent to accept a reply</p>;
  }
  if (availability === "unavailable") {
    return <p className={styles.note}>The agent can&apos;t take a reply right now</p>;
  }
  return null;
}
