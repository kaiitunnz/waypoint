"use client";

import { useCallback, useEffect, useState } from "react";

import { deleteAllAttachments, deleteAttachment, fetchSessionAttachments } from "../api";
import type { SessionAttachment } from "../types";

export interface SessionFiles {
  // Null while loading.
  items: SessionAttachment[] | null;
  error: string | null;
  // A delete-all is waiting for its confirmation.
  confirmingClear: boolean;
  setConfirmingClear: (confirming: boolean) => void;
  busy: boolean;
  // Each resolves false when the server did not delete.
  remove: (id: string) => Promise<boolean>;
  clearAll: () => Promise<boolean>;
}

// The session's stored files, loaded each time the view opens.
export function useSessionFiles(host: string, token: string, sessionId: string, open: boolean): SessionFiles {
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

  const remove = useCallback(
    async (id: string) => {
      setItems((prev) => (prev ? prev.filter((item) => item.id !== id) : prev));
      try {
        await deleteAttachment(host, token, sessionId, id);
        return true;
      } catch {
        void load(); // Resync on failure rather than lie about the state.
        return false;
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
      return true;
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to delete files");
      return false;
    } finally {
      setBusy(false);
    }
  }, [host, token, sessionId]);

  return { items, error, confirmingClear, setConfirmingClear, busy, remove, clearAll };
}
