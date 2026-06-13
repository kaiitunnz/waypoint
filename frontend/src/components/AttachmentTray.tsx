"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
} from "react";

import { attachmentUrl, uploadAttachment } from "@/lib/api";
import type { AttachmentSpec, EventRecord } from "@/lib/types";

export interface PendingAttachment {
  localId: string;
  name: string;
  isImage: boolean;
  status: "uploading" | "done" | "error";
  previewUrl?: string;
  spec?: AttachmentSpec;
  error?: string;
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
        return {
          localId: `att-${attachmentSeq++}`,
          name: file.name || "file",
          isImage,
          status: "uploading",
          previewUrl,
        };
      });
      setItems((prev) => [...prev, ...pending]);
      accepted.forEach((file, index) => {
        const { localId } = pending[index];
        uploadAttachment(host, token, sessionId, file)
          .then((spec) => {
            setItems((prev) =>
              prev.map((item) =>
                item.localId === localId
                  ? { ...item, status: "done", spec }
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
      });
    },
    [host, token, sessionId, onError],
  );

  const remove = useCallback(
    (localId: string) => {
      setItems((prev) => {
        revoke(prev.find((item) => item.localId === localId)?.previewUrl);
        return prev.filter((item) => item.localId !== localId);
      });
    },
    [revoke],
  );

  const clear = useCallback(() => {
    setItems((prev) => {
      for (const item of prev) revoke(item.previewUrl);
      return [];
    });
  }, [revoke]);

  const uploading = items.some((item) => item.status === "uploading");
  const readyIds = items
    .filter((item) => item.status === "done" && item.spec)
    .map((item) => (item.spec as AttachmentSpec).id);

  return {
    items,
    addFiles,
    remove,
    clear,
    uploading,
    readyIds,
    hasItems: items.length > 0,
  };
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

// Renders the attachments carried by a user message: images as thumbnails
// that open full size, other files as labelled links. Both hit the
// token-scoped serve endpoint.
export function MessageAttachments({ event }: { event: EventRecord }) {
  const ctx = useContext(AttachmentContext);
  const specs = attachmentSpecsFor(event);
  if (!ctx || specs.length === 0) return null;
  return (
    <div className="message-attachments">
      {specs.map((spec) => {
        const url = attachmentUrl(ctx.host, ctx.token, ctx.sessionId, spec.id);
        if (spec.kind === "image") {
          return (
            <a
              key={spec.id}
              href={url}
              target="_blank"
              rel="noreferrer"
              className="message-attachment-image"
              title={spec.filename}
            >
              {/* Token-query serve URL — next/image can't optimize it. */}
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={url} alt={spec.filename} loading="lazy" />
            </a>
          );
        }
        return (
          <a
            key={spec.id}
            href={url}
            target="_blank"
            rel="noreferrer"
            className="message-attachment-file"
            title={spec.filename}
          >
            <span className="attachment-glyph" aria-hidden="true">
              ▤
            </span>
            <span className="attachment-name">{spec.filename}</span>
          </a>
        );
      })}
    </div>
  );
}

export function AttachmentTray({
  items,
  onRemove,
}: {
  items: PendingAttachment[];
  onRemove: (localId: string) => void;
}) {
  if (items.length === 0) return null;
  return (
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
            <span className="attachment-glyph" aria-hidden="true">
              ▤
            </span>
          )}
          <span className="attachment-name">{item.name}</span>
          {item.status === "uploading" ? (
            <span className="attachment-spinner" aria-label="Uploading" />
          ) : null}
          {item.status === "error" ? (
            <span className="attachment-error" aria-hidden="true">
              !
            </span>
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
  );
}
