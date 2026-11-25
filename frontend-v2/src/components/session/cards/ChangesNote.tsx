"use client";

import type { EventDiffPreview } from "@waypoint/web-core/events";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";

import { DiffBlock } from "./DiffBlock";
import { RunLine } from "./RunLine";
import styles from "./ChangesNote.module.css";

// A note that carries a diff: one line on the route that opens to the changes.
export function ChangesNote({ event, preview, defaultOpen }: { event: EventRecord; preview: EventDiffPreview; defaultOpen: boolean }) {
  return (
    <RunLine
      defaultOpen={defaultOpen}
      phrase={event.text || "Changes"}
      stats={
        <>
          <span className={styles.add}>+{preview.totalAdditions}</span> <span className={styles.del}>−{preview.totalDeletions}</span>
        </>
      }
      trailing={<time>{formatEventTime(event.ts)}</time>}
    >
      <DiffBlock preview={preview} headers />
    </RunLine>
  );
}
