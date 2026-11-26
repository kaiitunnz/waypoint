"use client";

import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { sendUserFileInput } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { ActionChip } from "@/components/ui/ActionChip";

import { useSessionContext } from "../SessionContext";
import { Attachments } from "./Attachments";
import { EventTime } from "./EventTime";
import styles from "./SentFiles.module.css";

export function SentFiles({ event }: { event: EventRecord }) {
  const { openFiles } = useSessionContext();
  const specs = attachmentSpecsFor(event);
  const { files, caption } = sendUserFileInput(event);
  const count = specs.length || files.length;
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.glyph} aria-hidden="true">
          ↧
        </span>
        <span className={styles.title}>
          Sent you {count} file{count === 1 ? "" : "s"}
        </span>
        <EventTime ts={event.ts} className={styles.time} />
      </div>
      {caption ? <p className={styles.caption}>{caption}</p> : null}
      {specs.length > 0 ? (
        <Attachments specs={specs} />
      ) : (
        <ul className={styles.names}>
          {files.map((path, index) => (
            <li key={`${path}-${index}`}>{splitPath(path).base || path}</li>
          ))}
        </ul>
      )}
      {openFiles ? (
        <div className={styles.open}>
          <ActionChip icon="files" onClick={openFiles}>
            Open files
          </ActionChip>
        </div>
      ) : null}
    </div>
  );
}
