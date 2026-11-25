"use client";

import { reasoningPreview } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";

import { EventTime } from "./EventTime";
import { Markdown } from "./Markdown";
import { RunLine } from "./RunLine";
import styles from "./Reasoning.module.css";

export function Reasoning({ event }: { event: EventRecord }) {
  const preview = reasoningPreview(event.text);
  return (
    <RunLine
      phrase={
        <>
          Thinking{preview ? <span className={styles.preview}>{preview}</span> : null}
        </>
      }
      trailing={<EventTime ts={event.ts} />}
    >
      <div className={styles.text}>
        <Markdown text={event.text} />
      </div>
    </RunLine>
  );
}
