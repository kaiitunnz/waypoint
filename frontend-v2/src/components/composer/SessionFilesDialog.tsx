"use client";

import { attachmentUrl } from "@waypoint/web-core/api";
import { filesSummary, formatFileSize, typeTag } from "@waypoint/web-core/attachments";
import { useSessionFiles } from "@waypoint/web-core/hooks/useSessionFiles";
import type { SessionAttachment } from "@waypoint/web-core/types";
import { formatRelativeTime } from "@waypoint/web-core/usage";

import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Dialog } from "@/components/ui/Dialog";
import { FileThumb } from "@/components/ui/FileThumb";
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
  // The server no longer has these files.
  onDeleted: (ids: string[]) => void;
  // True while a send may still need a file.
  locked: boolean;
}

export function SessionFilesDialog({
  host,
  token,
  sessionId,
  open,
  onClose,
  onReference,
  referencedIds,
  onDeleted,
  locked,
}: SessionFilesDialogProps) {
  const { items, error, confirmingClear, setConfirmingClear, busy, remove, clearAll } = useSessionFiles(
    host,
    token,
    sessionId,
    open,
  );

  async function removeFile(id: string) {
    if (await remove(id)) onDeleted([id]);
  }
  async function removeAll() {
    const ids = (items ?? []).map((file) => file.id);
    if (await clearAll()) onDeleted(ids);
  }

  const footer =
    items && items.length > 0 ? (
      <Button variant="danger" busy={busy} disabled={locked} onClick={() => setConfirmingClear(true)}>
        {busy ? "Deleting…" : "Delete all"}
      </Button>
    ) : undefined;

  return (
    <>
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
                <FileThumb className={styles.thumb} src={file.kind === "image" ? url : null} />
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
                <IconButton label={`Delete ${file.filename}`} size="sm" disabled={locked} onClick={() => void removeFile(file.id)}>
                  <Icon name="trash" size={18} />
                </IconButton>
              </li>
            );
          })}
        </ul>
      </Dialog>
      <ConfirmDialog
        open={open && confirmingClear}
        title="Delete all files"
        detail="Every file uploaded to this session is deleted. This cannot be undone."
        confirmLabel="Delete all"
        onConfirm={removeAll}
        onClose={() => setConfirmingClear(false)}
      />
    </>
  );
}
