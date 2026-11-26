import { cardKindOf } from "@waypoint/web-core/session/cardKind";
import type { LeafItem } from "@waypoint/web-core/session/transcript";

import { ChangesNote } from "./ChangesNote";
import { EventTime } from "./EventTime";
import styles from "./RunNote.module.css";

// A note that came in with a run: attached, resumed, a compaction, or a set of changes.
export function RunNote({ child }: { child: LeafItem }) {
  if (child.kind !== "single") return null;
  const { event } = child;
  switch (cardKindOf(event)) {
    case "none":
      return null;
    case "diff_note":
      return <ChangesNote event={event} />;
    default:
      return (
        <div className={styles.note}>
          <span className={styles.noteText}>
            {event.text}
            {child.repeat ? ` ×${child.repeat.count}` : ""}
          </span>
          <EventTime ts={event.ts} until={child.repeat?.until} />
        </div>
      );
  }
}
