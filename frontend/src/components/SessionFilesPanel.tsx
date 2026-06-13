"use client";

import { useCallback, useEffect, useState } from "react";
import { createPortal } from "react-dom";

import { FileIcon, formatBytes, typeTag } from "@/components/AttachmentTray";
import {
  attachmentUrl,
  deleteAllAttachments,
  deleteAttachment,
  fetchSessionAttachments,
} from "@/lib/api";
import type { SessionAttachment } from "@/lib/types";
import { formatRelativeTime } from "@/lib/usage";

interface SessionFilesPanelProps {
  host: string;
  token: string;
  sessionId: string;
  open: boolean;
  onClose: () => void;
  // When set, each row offers an "add to message" action that references the
  // file in the composer (no re-upload). `referencedIds` are files already in
  // the tray, shown as added.
  onReference?: (spec: SessionAttachment) => void;
  referencedIds?: ReadonlySet<string>;
}

// Session-wide files manager: lists every attachment a session has stored and
// lets the user open, delete, or clear them. Mirrors SessionSwitcher's portal
// overlay (backdrop dismiss + Escape).
export function SessionFilesPanel({
  host,
  token,
  sessionId,
  open,
  onClose,
  onReference,
  referencedIds,
}: SessionFilesPanelProps) {
  const [items, setItems] = useState<SessionAttachment[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [confirmingClear, setConfirmingClear] = useState(false);
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setError(null);
    try {
      setItems(await fetchSessionAttachments(host, token, sessionId));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load files");
      setItems([]);
    }
  }, [host, token, sessionId]);

  useEffect(() => {
    if (!open) return;
    setItems(null);
    setConfirmingClear(false);
    void load();
  }, [open, load]);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  const removeOne = useCallback(
    async (id: string) => {
      setItems((prev) => (prev ? prev.filter((it) => it.id !== id) : prev));
      try {
        await deleteAttachment(host, token, sessionId, id);
      } catch {
        void load(); // Resync on failure rather than lie about the state.
      }
    },
    [host, token, sessionId, load],
  );

  const clearAll = useCallback(async () => {
    setBusy(true);
    try {
      await deleteAllAttachments(host, token, sessionId);
      setItems([]);
      setConfirmingClear(false);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete files");
    } finally {
      setBusy(false);
    }
  }, [host, token, sessionId]);

  if (!open || typeof document === "undefined") return null;

  const total = (items ?? []).reduce((sum, it) => sum + it.size, 0);
  const summary =
    items === null
      ? "Loading…"
      : `${items.length} file${items.length === 1 ? "" : "s"} · ${formatBytes(total)}`;

  return createPortal(
    <div
      className="session-files-backdrop"
      onPointerDown={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div
        className="session-files-modal"
        role="dialog"
        aria-modal="true"
        aria-label="Session files"
      >
        <div className="session-files-header">
          <div className="session-files-title">
            <span>Session files</span>
            <span className="session-files-count">{summary}</span>
          </div>
          <div className="session-files-header-actions">
            {items && items.length > 0 ? (
              confirmingClear ? (
                <>
                  <button
                    type="button"
                    className="session-files-confirm"
                    disabled={busy}
                    onClick={() => void clearAll()}
                  >
                    {busy ? "Deleting…" : "Delete all?"}
                  </button>
                  <button
                    type="button"
                    className="session-files-cancel"
                    onClick={() => setConfirmingClear(false)}
                  >
                    Cancel
                  </button>
                </>
              ) : (
                <button
                  type="button"
                  className="session-files-clear"
                  onClick={() => setConfirmingClear(true)}
                >
                  Delete all
                </button>
              )
            ) : null}
            <button
              type="button"
              className="session-files-close"
              aria-label="Close"
              onClick={onClose}
            >
              ×
            </button>
          </div>
        </div>

        <div className="session-files-list">
          {error ? <div className="session-files-empty">{error}</div> : null}
          {items === null && !error ? (
            <div className="session-files-empty">Loading files…</div>
          ) : null}
          {items !== null && items.length === 0 && !error ? (
            <div className="session-files-empty">
              No files uploaded to this session yet.
            </div>
          ) : null}
          {(items ?? []).map((it) => {
            const url = attachmentUrl(host, token, sessionId, it.id);
            return (
              <div className="session-files-row" key={it.id}>
                {it.kind === "image" ? (
                  // Token-query serve URL — next/image can't optimize it.
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    className="session-files-thumb"
                    src={url}
                    alt={it.filename}
                    loading="lazy"
                  />
                ) : (
                  <span className="session-files-tile" aria-hidden="true">
                    <FileIcon />
                  </span>
                )}
                <div className="session-files-body">
                  <span className="session-files-name" title={it.filename}>
                    {it.filename}
                  </span>
                  <span className="session-files-meta">
                    {typeTag(it.filename, it.kind === "image")} ·{" "}
                    {formatBytes(it.size)} ·{" "}
                    {formatRelativeTime(
                      new Date(it.uploaded_at * 1000).toISOString(),
                    )}
                  </span>
                </div>
                {onReference ? (
                  <button
                    type="button"
                    className="session-files-add"
                    disabled={referencedIds?.has(it.id)}
                    aria-label={`Add ${it.filename} to message`}
                    title={
                      referencedIds?.has(it.id)
                        ? "Added to message"
                        : "Add to message"
                    }
                    onClick={() => onReference(it)}
                  >
                    {referencedIds?.has(it.id) ? "✓" : "+"}
                  </button>
                ) : null}
                <a
                  className="session-files-open"
                  href={url}
                  target="_blank"
                  rel="noreferrer"
                  aria-label={`Open ${it.filename}`}
                  title="Open in new tab"
                >
                  ↗
                </a>
                <button
                  type="button"
                  className="session-files-delete"
                  aria-label={`Delete ${it.filename}`}
                  title="Delete"
                  onClick={() => void removeOne(it.id)}
                >
                  ×
                </button>
              </div>
            );
          })}
        </div>
      </div>
    </div>,
    document.body,
  );
}
