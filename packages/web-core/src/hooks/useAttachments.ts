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
  const urlsRef = useRef<Set<string>>(new Set());
  // Source File kept per localId so a failed upload can be retried.
  const filesRef = useRef<Map<string, File>>(new Map());
  // Unsent uploads, to free if the page goes first. A ref, so the handler below
  // reads the latest without re-subscribing.
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

  // Eager uploads leak a server blob if the page closes before send, so unsent
  // ones are freed on unmount and on page hide, with keepalive to survive the
  // teardown. The server's TTL sweep covers crashes and being offline.
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

  // A file the session already stores: nothing uploads, and its preview is a
  // server URL, not an object URL to revoke.
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

  // Best-effort. A referenced item points at a file the session owns, so its
  // blob is never deleted.
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

  // Empties the tray but keeps the server blobs, which belong to a sent message.
  const clear = useCallback(() => {
    setItems((prev) => {
      for (const item of prev) revoke(item.previewUrl);
      filesRef.current.clear();
      return [];
    });
  }, [revoke]);

  // Like `clear`, for the given files only: a send leaves any added since.
  const release = useCallback(
    (ids: readonly string[]) => {
      setItems((prev) =>
        prev.filter((item) => {
          const sent = item.spec !== undefined && ids.includes(item.spec.id);
          if (sent) {
            revoke(item.previewUrl);
            filesRef.current.delete(item.localId);
          }
          return !sent;
        }),
      );
    },
    [revoke],
  );

  // Frees the server blobs too: only for files that were never sent.
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
    release,
    discardAll,
    uploading,
    readyIds,
    hasItems: items.length > 0,
  };
}
