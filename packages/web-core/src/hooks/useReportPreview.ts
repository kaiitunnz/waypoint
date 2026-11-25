"use client";

import { useEffect, useState } from "react";

import { fetchAttachmentPreview, type AttachmentPreview } from "../api";
import type { ReportPreviewState } from "../session/taskNoteBody";
import type { AttachmentSpec } from "../types";

// Previews are immutable per attachment id; one fetch per id per page life.
const previewCache = new Map<string, AttachmentPreview>();

// Lazily reads a bounded text prefix of a captured report once `open`. The
// request is abandoned when the card collapses or unmounts, and a failure only
// ever costs the preview; the attachment link stays.
export function useReportPreview(
  credentials: { host: string; token: string; sessionId: string } | null,
  spec: AttachmentSpec | null,
  open: boolean,
): ReportPreviewState | null {
  const attachmentId = spec?.id ?? null;
  const cached = attachmentId ? previewCache.get(attachmentId) : undefined;
  const [state, setState] = useState<ReportPreviewState | null>(
    cached ? { status: "ready", preview: cached } : null,
  );
  const host = credentials?.host;
  const token = credentials?.token;
  const sessionId = credentials?.sessionId;

  useEffect(() => {
    if (!open || !host || !token || !sessionId || !attachmentId) return;
    const hit = previewCache.get(attachmentId);
    if (hit) {
      setState({ status: "ready", preview: hit });
      return;
    }
    const controller = new AbortController();
    setState({ status: "loading" });
    fetchAttachmentPreview(host, token, sessionId, attachmentId, { signal: controller.signal })
      .then((preview) => {
        previewCache.set(attachmentId, preview);
        if (!controller.signal.aborted) setState({ status: "ready", preview });
      })
      .catch(() => {
        // A 404 from a backend without the route lands here too.
        if (!controller.signal.aborted) setState({ status: "error" });
      });
    return () => controller.abort();
  }, [open, host, token, sessionId, attachmentId]);

  return state;
}
