import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";

import { Attachments } from "./Attachments";
import { CopyButton } from "./CopyButton";
import styles from "./HeuristicCard.module.css";

// An event of a kind with no card of its own, such as raw terminal output.
export function HeuristicCard({ event }: { event: EventRecord }) {
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.kind}>{event.kind.replaceAll("_", " ")}</span>
        <time className={styles.time}>{formatEventTime(event.ts)}</time>
        <CopyButton text={event.text} />
      </div>
      <pre className={styles.text}>{event.text}</pre>
      <Attachments specs={attachmentSpecsFor(event)} />
    </div>
  );
}
