"use client";

import { attachmentUrl } from "@waypoint/web-core/api";
import { formatFileSize } from "@waypoint/web-core/attachments";
import type { AttachmentSpec } from "@waypoint/web-core/types";

import { FileThumb } from "@/components/ui/FileThumb";

import { useSessionContext } from "../SessionContext";
import styles from "./Attachments.module.css";

function AttachmentCard({ spec, url }: { spec: AttachmentSpec; url: string }) {
  return (
    <a href={url} target="_blank" rel="noreferrer" className={styles.card} title={spec.filename}>
      <FileThumb className={styles.thumb} src={spec.kind === "image" ? url : null} alt={spec.filename} mark="▤" />
      <span className={styles.text}>
        <span className={styles.name}>{spec.filename}</span>
        <span className={styles.size}>{formatFileSize(spec.size)}</span>
      </span>
    </a>
  );
}

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
