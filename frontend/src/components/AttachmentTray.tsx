"use client";

import { createContext, type MouseEvent, useContext, useState } from "react";

import { attachmentUrl } from "@waypoint/web-core/api";
import { filesSummary, formatFileSize, typeTag } from "@waypoint/web-core/attachments";
import type { PendingAttachment } from "@waypoint/web-core/hooks/useAttachments";
import type { AttachmentSpec } from "@waypoint/web-core/types";

// Crisp monochrome glyphs that inherit `currentColor`, so they sit alongside
// the composer's brass action buttons instead of an off-palette emoji.
export function PaperclipIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 11.5 12 20.5a5 5 0 0 1-7-7l8.5-8.5a3.3 3.3 0 0 1 4.7 4.7l-8.6 8.6a1.7 1.7 0 0 1-2.4-2.4l7.8-7.8" />
    </svg>
  );
}

export function FileIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="15"
      height="15"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M13 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V9z" />
      <path d="M13 3v6h6" />
    </svg>
  );
}

// Folder glyph for the workspace file-explorer button in the composer.
export function FolderIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M2 9a2 2 0 0 1 2-2h5l2 2h9a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H4a2 2 0 0 1-2-2z" />
      <path d="M2 12h20" />
    </svg>
  );
}

// Folder glyph for the "session files" manager entry point.
export function FilesIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="16"
      height="16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
    </svg>
  );
}

interface AttachmentContextValue {
  host: string;
  token: string;
  sessionId: string;
}

// Supplies the credentials needed to build authenticated attachment URLs to
// the transcript subtree, so individual cards don't have to prop-drill them.
const AttachmentContext = createContext<AttachmentContextValue | null>(null);
export const AttachmentContextProvider = AttachmentContext.Provider;

// Read the transcript subtree's attachment credentials. The context object
// stays private so callers can only take the whole value, never redefine it.
export function useAttachmentContext(): AttachmentContextValue | null {
  return useContext(AttachmentContext);
}

// One compact card for a sent message's attachment — a small thumbnail
// (images) or file glyph plus name and size. A thumbnail whose blob was since
// deleted via the files manager 404s, so it falls back to the file glyph
// instead of a broken image. Tapping opens the full file in a new tab.
function MessageAttachmentCard({
  spec,
  url,
}: {
  spec: AttachmentSpec;
  url: string;
}) {
  const [imageBroken, setImageBroken] = useState(false);
  return (
    <a
      href={url}
      target="_blank"
      rel="noreferrer"
      className="message-attachment-file"
      title={spec.filename}
    >
      {spec.kind === "image" && !imageBroken ? (
        // Token-query serve URL — next/image can't optimize it.
        // eslint-disable-next-line @next/next/no-img-element
        <img
          className="message-attachment-thumb"
          src={url}
          alt={spec.filename}
          loading="lazy"
          onError={() => setImageBroken(true)}
        />
      ) : (
        <span className="attachment-tile" aria-hidden="true">
          <FileIcon />
        </span>
      )}
      <span className="attachment-body">
        <span className="attachment-name">{spec.filename}</span>
        <span className="attachment-meta">{formatFileSize(spec.size)}</span>
      </span>
    </a>
  );
}

// Renders the attachments carried by a user message as compact cards.
export function MessageAttachments({ specs }: { specs: AttachmentSpec[] }) {
  const ctx = useContext(AttachmentContext);
  if (!ctx || specs.length === 0) return null;
  return (
    <div className="message-attachments">
      {specs.map((spec) => (
        <MessageAttachmentCard
          key={spec.id}
          spec={spec}
          url={attachmentUrl(ctx.host, ctx.token, ctx.sessionId, spec.id)}
        />
      ))}
    </div>
  );
}

// A tray button that takes focus collapses the composer's leading buttons, which
// reflows the tray and can move the button before its click lands.
const keepFocus = (event: MouseEvent) => event.preventDefault();

export function AttachmentTray({
  items,
  onRemove,
  onRetry,
  onClear,
}: {
  items: PendingAttachment[];
  onRemove: (localId: string) => void;
  onRetry: (localId: string) => void;
  onClear: () => void;
}) {
  if (items.length === 0) return null;
  const summary = filesSummary(items);
  return (
    <div className="attachment-tray-wrap">
      <div className="attachment-tray-header">
        <span className="attachment-tray-count">{summary}</span>
        <button
          type="button"
          className="attachment-clear"
          onMouseDown={keepFocus}
          onClick={onClear}
        >
          Clear all
        </button>
      </div>
      <div className="attachment-tray" role="list">
        {items.map((item) => (
          <div
            key={item.localId}
            className={`attachment-chip is-${item.status}${item.referenced ? " is-referenced" : ""}`}
            role="listitem"
            title={
              item.error ??
              (item.referenced ? `${item.name} (linked)` : item.name)
            }
          >
            {item.isImage && item.previewUrl ? (
              // Local object URL preview — next/image can't optimize blob URLs.
              // eslint-disable-next-line @next/next/no-img-element
              <img
                className="attachment-thumb"
                src={item.previewUrl}
                alt={item.name}
              />
            ) : (
              <span className="attachment-tile" aria-hidden="true">
                <FileIcon />
              </span>
            )}
            <span className="attachment-body">
              <span className="attachment-name">{item.name}</span>
              <span className="attachment-meta">
                {item.status === "error"
                  ? "Upload failed"
                  : `${item.referenced ? "↪ " : ""}${typeTag(item.name, item.isImage)} · ${formatFileSize(item.size)}`}
              </span>
            </span>
            {item.status === "uploading" ? (
              <span className="attachment-spinner" aria-label="Uploading" />
            ) : null}
            {item.status === "error" ? (
              <button
                type="button"
                className="attachment-retry"
                aria-label={`Retry ${item.name}`}
                title="Retry upload"
                onMouseDown={keepFocus}
                onClick={() => onRetry(item.localId)}
              >
                ↻
              </button>
            ) : null}
            <button
              type="button"
              className="attachment-remove"
              aria-label={`Remove ${item.name}`}
              onMouseDown={keepFocus}
              onClick={() => onRemove(item.localId)}
            >
              ×
            </button>
          </div>
        ))}
      </div>
    </div>
  );
}
