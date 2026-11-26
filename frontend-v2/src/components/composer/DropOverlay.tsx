"use client";

import { filesFromDataTransfer } from "@waypoint/web-core/fileDrop";

import { cx } from "@/lib/cx";

import styles from "./DropOverlay.module.css";

interface DropOverlayProps {
  // Why the session takes no files, or null when it does.
  blocked: string | null;
  sessionTitle: string;
  onFiles: (files: File[]) => void;
}

export function DropOverlay({ blocked, sessionTitle, onFiles }: DropOverlayProps) {
  return (
    <div
      className={cx(styles.overlay, blocked !== null && styles.blocked)}
      onDragOver={(event) => {
        event.preventDefault();
        event.dataTransfer.dropEffect = blocked === null ? "copy" : "none";
      }}
      onDrop={(event) => {
        event.preventDefault();
        if (blocked !== null) return;
        const files = filesFromDataTransfer(event.dataTransfer);
        if (files.length) onFiles(files);
      }}
    >
      <div className={styles.frame}>
        {blocked === null ? (
          <>
            <p className={styles.title}>Drop to attach</p>
            <p className={styles.detail}>to your reply in {sessionTitle}</p>
          </>
        ) : (
          <>
            <p className={styles.title}>Can’t attach here</p>
            <p className={styles.detail}>{blocked}</p>
          </>
        )}
      </div>
    </div>
  );
}
