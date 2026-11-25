"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { attachmentUrl, deleteAttachment, uploadAttachment } from "../api";
import type { AttachmentSpec } from "../types";

export interface PendingAttachment {
  localId: string;
  name: string;
  size: number;
  isImage: boolean;
  status: "uploading" | "done" | "error";
  previewUrl?: string;
  spec?: AttachmentSpec;
  error?: string;
  // True when this is a reference to a file the session already stores (added
  // via the picker or @-mention) rather than a fresh upload — removing it must
  // not delete the shared blob.
  referenced?: boolean;
}

interface UseAttachmentsArgs {
  host: string;
  token: string;
  sessionId: string;
  onError?: (message: string) => void;
  // Pin uploads at creation time (inbox reply blobs), so the requesting
  // session can read them back after the orphan TTL sweep.
  pin?: boolean;
}

let attachmentSeq = 0;

export function useAttachments({
  host,
  token,
  sessionId,
  onError,
  pin = false,
}: UseAttachmentsArgs) {
  const [items, setItems] = useState<PendingAttachment[]>([]);
  // Object URLs created for image previews, revoked on removal/unmount.
  const urlsRef = useRef<Set<string>>(new Set());
  // Source File kept per localId so a failed upload can be retried.
  const filesRef = useRef<Map<string, File>>(new Map());
  // Unsent, non-referenced uploaded blob ids — the orphans to free if the user
  // leaves before sending. Kept in a ref so the unmount/pagehide handler sees
  // the latest set without re-subscribing.
  const orphanRef = useRef<string[]>([]);
  const credsRef = useRef({ host, token, sessionId });

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

  useEffect(() => {
    credsRef.current = { host, token, sessionId };
    orphanRef.current = items
      .filter((item) => item.spec && !item.referenced)
      .map((item) => (item.spec as AttachmentSpec).id);
  }, [items, host, token, sessionId]);

  // Eager uploads leak a server blob if the page closes before send. Free the
  // unsent ones on unmount (SPA navigation / view switch) and on page hide
  // (hard close) with keepalive so the request survives teardown; a sent turn
  // clears the tray first, so its blobs are never in `orphanRef`. The server's
  // TTL sweep is the backstop for crashes / offline.
  useEffect(() => {
    const flush = () => {
      const { host: h, token: t, sessionId: s } = credsRef.current;
      for (const id of orphanRef.current) {
        void fetch(`${h}/api/sessions/${s}/attachments/${id}`, {
          method: "DELETE",
          headers: { Authorization: `Bearer ${t}` },
          keepalive: true,
        }).catch(() => {});
      }
      orphanRef.current = [];
    };
    window.addEventListener("pagehide", flush);
    return () => {
      window.removeEventListener("pagehide", flush);
      flush();
    };
  }, []);

  const startUpload = useCallback(
    (localId: string) => {
      const file = filesRef.current.get(localId);
      if (!file) return;
      uploadAttachment(host, token, sessionId, file, { pin })
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
    [host, token, sessionId, onError, pin],
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

  // Add a file the session already stores as a referenced attachment — no
  // upload. Its id rides on send like any other; the image preview points at
  // the server's serve endpoint (a plain URL, never an object URL to revoke).
  const referenceExisting = useCallback(
    (spec: AttachmentSpec) => {
      setItems((prev) => {
        if (prev.some((item) => item.spec?.id === spec.id)) return prev;
        const isImage = spec.kind === "image";
        return [
          ...prev,
          {
            localId: `att-${attachmentSeq++}`,
            name: spec.filename,
            size: spec.size,
            isImage,
            status: "done",
            spec,
            referenced: true,
            previewUrl: isImage
              ? attachmentUrl(host, token, sessionId, spec.id)
              : undefined,
          },
        ];
      });
    },
    [host, token, sessionId],
  );

  // Free the server blob for a removed eager upload so it doesn't orphan;
  // best-effort. A *referenced* item is just a pointer to a file the session
  // already owns, so removing it must never delete that shared blob.
  const discardServerSide = useCallback(
    (item: PendingAttachment) => {
      if (item.spec && !item.referenced) {
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
  // Every session-file id currently in the tray, so a picker can mark files
  // that are already added.
  const attachedIds = new Set(
    items.flatMap((item) => (item.spec ? [item.spec.id] : [])),
  );

  return {
    items,
    addFiles,
    remove,
    retry,
    referenceExisting,
    attachedIds,
    clear,
    discardAll,
    uploading,
    readyIds,
    hasItems: items.length > 0,
  };
}
