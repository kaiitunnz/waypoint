import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import type { EventRecord } from "@waypoint/web-core/types";

import { Attachments } from "./Attachments";
import { CopyButton } from "./CopyButton";
import { EventTime } from "./EventTime";
import { FoldedText } from "./FoldedText";
import styles from "./HeuristicCard.module.css";

// An event of a kind with no card of its own, such as raw terminal output.
export function HeuristicCard({ event }: { event: EventRecord }) {
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.kind}>{event.kind.replaceAll("_", " ")}</span>
        <EventTime ts={event.ts} className={styles.time} />
        <CopyButton text={event.text} />
      </div>
      <FoldedText text={event.text} keep="start" className={styles.text} />
      <Attachments specs={attachmentSpecsFor(event)} />
    </div>
  );
}
