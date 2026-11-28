"use client";

import { attachmentUrl } from "@waypoint/web-core/api";
import { typeTag } from "@waypoint/web-core/attachments";
import type { InboxAttachmentRef } from "@waypoint/web-core/types";

import { FileName } from "@/components/ui/FileName";
import { FileThumb } from "@/components/ui/FileThumb";
import { Icon } from "@/components/ui/Icon";
import { useShellData } from "@/lib/shell-data";

import styles from "./InboxFiles.module.css";

// A ref with no known kind is tried as an image; a failed one shows the file mark.
function useFile(ref: InboxAttachmentRef) {
  const { host, token } = useShellData();
  const url = attachmentUrl(host, token, ref.session_id, ref.attachment_id);
  const image = ref.kind == null || ref.kind === "image";
  return { url, name: ref.filename ?? ref.attachment_id, preview: image ? url : null, image: ref.kind === "image" };
}

export function FileTile({ attachment }: { attachment: InboxAttachmentRef }) {
  const { url, name, preview, image } = useFile(attachment);
  return (
    <a className={styles.tile} href={url} target="_blank" rel="noreferrer" title={name}>
      <FileThumb className={styles.thumb} src={preview} />
      <span className={styles.text}>
        <FileName name={name} className={styles.name} />
        <span className={styles.type}>{typeTag(name, image)}</span>
      </span>
      <span className={styles.open}>
        <Icon name="external" size={16} />
      </span>
    </a>
  );
}

function FileChip({ attachment }: { attachment: InboxAttachmentRef }) {
  const { url, name, preview } = useFile(attachment);
  return (
    <a className={styles.chip} href={url} target="_blank" rel="noreferrer" title={name}>
      <FileName name={name} className={styles.chipName} />
      <FileThumb className={styles.small} src={preview} />
    </a>
  );
}

export function FileChips({ refs }: { refs: InboxAttachmentRef[] }) {
  if (refs.length === 0) return null;
  return (
    <span className={styles.chips}>
      {refs.map((ref) => (
        <FileChip key={`${ref.session_id}:${ref.attachment_id}`} attachment={ref} />
      ))}
    </span>
  );
}
