"use client";

import Link from "next/link";

import { attachmentSpecsFor } from "@waypoint/web-core/attachments";
import { formatEventTime, sendUserFileInput } from "@waypoint/web-core/session/cardText";
import { sessionHref } from "@waypoint/web-core/shellNav";
import type { EventRecord } from "@waypoint/web-core/types";

import { useSessionContext } from "../SessionContext";
import { Attachments } from "./Attachments";
import styles from "./SentFiles.module.css";

// Files the agent handed to you.
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
            <li key={`${path}-${index}`}>{path.split(/[\\/]/).pop() || path}</li>
          ))}
        </ul>
      )}
      <Link href={`${sessionHref(sessionId)}/files`} className={styles.open}>
        Open files
      </Link>
    </div>
  );
}
