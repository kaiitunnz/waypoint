"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { attachmentUrl, deleteAttachment, uploadAttachment } from "@/lib/api";
import type { AttachmentSpec, EventRecord } from "@/lib/types";

export interface PendingAttachment {
  localId: string;
  name: string;
  size: number;
  isImage: boolean;
  status: "uploading" | "done" | "error";
  previewUrl?: string;
  spec?: AttachmentSpec;
  error?: string;
}

// Human-readable byte size, e.g. "812 B", "2.4 MB".
export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  const units = ["KB", "MB", "GB", "TB"];
  let value = bytes / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value < 10 ? value.toFixed(1) : Math.round(value)} ${units[unit]}`;
}

// A short type tag for a chip — the uppercased extension, else IMAGE/FILE.
export function typeTag(filename: string, isImage: boolean): string {
  const dot = filename.lastIndexOf(".");
  const ext =
    dot > 0 && dot < filename.length - 1 ? filename.slice(dot + 1) : "";
  if (ext && ext.length <= 5) return ext.toUpperCase();
  return isImage ? "IMAGE" : "FILE";
}

interface UseAttachmentsArgs {
  host: string;
  token: string;
  sessionId: string;
  onError?: (message: string) => void;
}

let attachmentSeq = 0;

// Extract files from a paste/drop event's DataTransfer, preferring the
// richer `items` API (which carries pasted screenshots) and falling back to
// `files` for plain drops.
export function filesFromDataTransfer(data: DataTransfer | null): File[] {
  if (!data) return [];
  const out: File[] = [];
  if (data.items && data.items.length > 0) {
    for (const item of Array.from(data.items)) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) out.push(file);
      }
    }
  } else if (data.files && data.files.length > 0) {
    out.push(...Array.from(data.files));
  }
  return out;
}

export function useAttachments({
  host,
  token,
  sessionId,
  onError,
}: UseAttachmentsArgs) {
  const [items, setItems] = useState<PendingAttachment[]>([]);
  // Object URLs created for image previews, revoked on removal/unmount.
  const urlsRef = useRef<Set<string>>(new Set());
  // Source File kept per localId so a failed upload can be retried.
  const filesRef = useRef<Map<string, File>>(new Map());

  const revoke = useCallback((url?: string) => {
    if (url && urlsRef.current.has(url)) {
      URL.revokeObjectURL(url);
      urlsRef.current.delete(url);
    }
  }, []);

  useEffect(() => {
    const urls = urlsRef.current;
    return () => {
      for (const url of urls) URL.revokeObjectURL(url);
      urls.clear();
    };
  }, []);

  const startUpload = useCallback(
    (localId: string) => {
      const file = filesRef.current.get(localId);
      if (!file) return;
      uploadAttachment(host, token, sessionId, file)
        .then((spec) => {
          setItems((prev) =>
            prev.map((item) =>
              item.localId === localId
                ? { ...item, status: "done", spec, error: undefined }
                : item,
            ),
          );
        })
        .catch((error) => {
          const message =
            error instanceof Error ? error.message : "upload failed";
          setItems((prev) =>
            prev.map((item) =>
              item.localId === localId
                ? { ...item, status: "error", error: message }
                : item,
            ),
          );
          onError?.(message);
        });
    },
    [host, token, sessionId, onError],
  );

  const addFiles = useCallback(
    (files: File[]) => {
      const accepted = files.filter((file) => file.size > 0);
      if (accepted.length === 0) return;
      const pending = accepted.map<PendingAttachment>((file) => {
        const isImage = file.type.startsWith("image/");
        let previewUrl: string | undefined;
        if (isImage) {
          previewUrl = URL.createObjectURL(file);
          urlsRef.current.add(previewUrl);
        }
        const localId = `att-${attachmentSeq++}`;
        filesRef.current.set(localId, file);
        return {
          localId,
          name: file.name || "file",
          size: file.size,
          isImage,
          status: "uploading",
          previewUrl,
        };
      });
      setItems((prev) => [...prev, ...pending]);
      for (const item of pending) startUpload(item.localId);
    },
    [startUpload],
  );

  const retry = useCallback(
    (localId: string) => {
      setItems((prev) =>
        prev.map((item) =>
          item.localId === localId
            ? { ...item, status: "uploading", error: undefined }
            : item,
        ),
      );
      startUpload(localId);
    },
    [startUpload],
  );

  // Free the server blob for any uploaded item so removed eager uploads don't
  // orphan; best-effort, so a failed cleanup never blocks removal.
  const discardServerSide = useCallback(
    (item: PendingAttachment) => {
      if (item.spec) {
        void deleteAttachment(host, token, sessionId, item.spec.id).catch(
          () => {},
        );
      }
      filesRef.current.delete(item.localId);
    },
    [host, token, sessionId],
  );

  const remove = useCallback(
    (localId: string) => {
      setItems((prev) => {
        const target = prev.find((item) => item.localId === localId);
        if (target) {
          revoke(target.previewUrl);
          discardServerSide(target);
        }
        return prev.filter((item) => item.localId !== localId);
      });
    },
    [revoke, discardServerSide],
  );

  // Reset the tray WITHOUT touching the server — used after a successful send,
  // where the blobs now belong to the sent message and must survive.
  const clear = useCallback(() => {
    setItems((prev) => {
      for (const item of prev) revoke(item.previewUrl);
      filesRef.current.clear();
      return [];
    });
  }, [revoke]);

  // Discard every pending attachment AND free its server blob — the explicit
  // "Clear all" control, only ever used before the message is sent.
  const discardAll = useCallback(() => {
    setItems((prev) => {
      for (const item of prev) {
        revoke(item.previewUrl);
        discardServerSide(item);
      }
      return [];
    });
  }, [revoke, discardServerSide]);

  const uploading = items.some((item) => item.status === "uploading");
  const readyIds = items
    .filter((item) => item.status === "done" && item.spec)
    .map((item) => (item.spec as AttachmentSpec).id);

  return {
    items,
    addFiles,
    remove,
    retry,
    clear,
    discardAll,
    uploading,
    readyIds,
    hasItems: items.length > 0,
  };
}

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

function attachmentSpecsFor(event: EventRecord): AttachmentSpec[] {
  const raw = event.metadata?.attachments;
  return Array.isArray(raw) ? (raw as AttachmentSpec[]) : [];
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
        <span className="attachment-meta">{formatBytes(spec.size)}</span>
      </span>
    </a>
  );
}

// Renders the attachments carried by a user message as compact cards.
export function MessageAttachments({ event }: { event: EventRecord }) {
  const ctx = useContext(AttachmentContext);
  const specs = attachmentSpecsFor(event);
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
  const total = items.reduce((sum, item) => sum + item.size, 0);
  const summary = `${items.length} file${items.length === 1 ? "" : "s"} · ${formatBytes(total)}`;
  return (
    <div className="attachment-tray-wrap">
      <div className="attachment-tray-header">
        <span className="attachment-tray-count">{summary}</span>
        <button type="button" className="attachment-clear" onClick={onClear}>
          Clear all
        </button>
      </div>
      <div className="attachment-tray" role="list">
        {items.map((item) => (
          <div
            key={item.localId}
            className={`attachment-chip is-${item.status}`}
            role="listitem"
            title={item.error ?? item.name}
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
                  : `${typeTag(item.name, item.isImage)} · ${formatBytes(item.size)}`}
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
                onClick={() => onRetry(item.localId)}
              >
                ↻
              </button>
            ) : null}
            <button
              type="button"
              className="attachment-remove"
              aria-label={`Remove ${item.name}`}
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
