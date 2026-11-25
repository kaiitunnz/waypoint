"use client";

import { attachmentUrl } from "@waypoint/web-core/api";
import { filesSummary, formatFileSize, typeTag } from "@waypoint/web-core/attachments";
import { useSessionFiles } from "@waypoint/web-core/hooks/useSessionFiles";
import type { SessionAttachment } from "@waypoint/web-core/types";
import { formatRelativeTime } from "@waypoint/web-core/usage";

import { Button } from "@/components/ui/Button";
import { Dialog } from "@/components/ui/Dialog";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";

import styles from "./SessionFilesDialog.module.css";

interface SessionFilesDialogProps {
  host: string;
  token: string;
  sessionId: string;
  open: boolean;
  onClose: () => void;
  // Adds a stored file to the draft without uploading it again.
  onReference: (file: SessionAttachment) => void;
  referencedIds: ReadonlySet<string>;
}

export function SessionFilesDialog({
  host,
  token,
  sessionId,
  open,
  onClose,
  onReference,
  referencedIds,
}: SessionFilesDialogProps) {
  const { items, error, confirmingClear, setConfirmingClear, busy, remove, clearAll } = useSessionFiles(
    host,
    token,
    sessionId,
    open,
  );

  const footer =
    items && items.length > 0 ? (
      confirmingClear ? (
        <>
          <Button variant="text" onClick={() => setConfirmingClear(false)}>
            Cancel
          </Button>
          <Button variant="danger" busy={busy} onClick={() => void clearAll()}>
            {busy ? "Deleting…" : "Delete all?"}
          </Button>
        </>
      ) : (
        <Button variant="danger" onClick={() => setConfirmingClear(true)}>
          Delete all
        </Button>
      )
    ) : undefined;

  return (
    <Dialog
      open={open}
      onClose={onClose}
      title="Session files"
      subtitle={items ? filesSummary(items) : "Loading…"}
      size="lg"
      footer={footer}
      flush
    >
      {error ? <p className={styles.empty}>{error}</p> : null}
      {items === null && !error ? <p className={styles.empty}>Loading files…</p> : null}
      {items !== null && items.length === 0 && !error ? (
        <p className={styles.empty}>No files uploaded to this session yet.</p>
      ) : null}
      <ul className={styles.list}>
        {(items ?? []).map((file) => {
          const url = attachmentUrl(host, token, sessionId, file.id);
          const added = referencedIds.has(file.id);
          return (
            <li key={file.id} className={styles.row}>
              {file.kind === "image" ? (
                // A token-query serve URL: next/image cannot optimise it.
                // eslint-disable-next-line @next/next/no-img-element
                <img className={styles.thumb} src={url} alt="" loading="lazy" />
              ) : (
                <span className={styles.tile}>
                  <Icon name="file" size={18} />
                </span>
              )}
              <span className={styles.text}>
                <span className={styles.name} title={file.filename}>
                  {file.filename}
                </span>
                <span className={styles.meta}>
                  {file.origin === "task_output" ? <span className={styles.origin}>task report</span> : null}
                  <span>{typeTag(file.filename, file.kind === "image")}</span>
                  <span>{formatFileSize(file.size)}</span>
                  <span>{formatRelativeTime(new Date(file.uploaded_at * 1000).toISOString())}</span>
                </span>
              </span>
              <IconButton
                label={added ? `${file.filename} added to message` : `Add ${file.filename} to message`}
                size="sm"
                disabled={added}
                onClick={() => onReference(file)}
              >
                <Icon name={added ? "check" : "plus"} size={18} />
              </IconButton>
              <a
                className={styles.open}
                href={url}
                target="_blank"
                rel="noreferrer"
                aria-label={`Open ${file.filename}`}
                title="Open in new tab"
              >
                <Icon name="external" size={18} />
              </a>
              <IconButton label={`Delete ${file.filename}`} size="sm" onClick={() => void remove(file.id)}>
                <Icon name="trash" size={18} />
              </IconButton>
            </li>
          );
        })}
      </ul>
    </Dialog>
  );
}
