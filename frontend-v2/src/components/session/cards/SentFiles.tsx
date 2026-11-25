"use client";

import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { formatEventTime, sendUserFileInput } from "@waypoint/web-core/session/cardText";
import { sessionFilesHref } from "@waypoint/web-core/shellNav";
import type { EventRecord } from "@waypoint/web-core/types";
import { splitPath } from "@waypoint/web-core/workspacePaths";

import { ActionChipLink } from "@/components/ui/ActionChip";

import { useSessionContext } from "../SessionContext";
import { Attachments } from "./Attachments";
import styles from "./SentFiles.module.css";

export function SentFiles({ event }: { event: EventRecord }) {
  const { sessionId } = useSessionContext();
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
        <time className={styles.time}>{formatEventTime(event.ts)}</time>
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
      <div className={styles.open}>
        <ActionChipLink href={sessionFilesHref(sessionId)} icon="chevron-right">
          Open files
        </ActionChipLink>
      </div>
    </div>
  );
}
