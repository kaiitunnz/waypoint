"use client";

import { useState } from "react";

import { attachmentUrl } from "@waypoint/web-core/api";
import { formatFileSize } from "@waypoint/web-core/attachments";
import type { AttachmentSpec } from "@waypoint/web-core/types";

import { useSessionContext } from "../SessionContext";
import styles from "./Attachments.module.css";

function AttachmentCard({ spec, url }: { spec: AttachmentSpec; url: string }) {
  const [broken, setBroken] = useState(false);
  return (
    <a href={url} target="_blank" rel="noreferrer" className={styles.card} title={spec.filename}>
      {spec.kind === "image" && !broken ? (
        // A token-query serve URL: next/image cannot optimise it.
        // eslint-disable-next-line @next/next/no-img-element
        <img className={styles.thumb} src={url} alt={spec.filename} loading="lazy" onError={() => setBroken(true)} />
      ) : (
        <span className={styles.tile} aria-hidden="true">
          ▤
        </span>
      )}
      <span className={styles.text}>
        <span className={styles.name}>{spec.filename}</span>
        <span className={styles.size}>{formatFileSize(spec.size)}</span>
      </span>
    </a>
  );
}

// The files a message carries, as compact cards that open the file.
export function Attachments({ specs }: { specs: AttachmentSpec[] }) {
  const { host, token, sessionId } = useSessionContext();
  if (specs.length === 0) return null;
  return (
    <div className={styles.list}>
      {specs.map((spec) => (
        <AttachmentCard key={spec.id} spec={spec} url={attachmentUrl(host, token, sessionId, spec.id)} />
      ))}
    </div>
  );
}
