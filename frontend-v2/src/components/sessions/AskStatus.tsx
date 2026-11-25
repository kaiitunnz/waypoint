import type { PendingAsk } from "@waypoint/web-core/types";

import type { AskPhase } from "./useAskResponder";
import styles from "./AskStatus.module.css";

interface AskStatusProps {
  availability: PendingAsk["availability"];
  phase: AskPhase;
}

export function AskStatus({ availability, phase }: AskStatusProps) {
  if (phase.state === "error") {
    return (
      <p className={styles.error} role="alert">
        {phase.message}
      </p>
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
