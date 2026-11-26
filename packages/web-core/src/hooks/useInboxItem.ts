"use client";

import { useCallback, useEffect, useState } from "react";

import { connectInboxSocket, deleteInboxItem, fetchInboxItem, isAuthError, markInboxRead } from "../api";
import { fresherInboxItem } from "../inbox";
import type { InboxItem, SessionEnvelope } from "../types";

interface InboxItemOptions {
  host: string;
  token: string;
  itemId: string;
  // Must be stable: a new one re-reads the item and restarts its socket.
  onAuthFailure: () => void;
}

export interface InboxItemState {
  // Null while loading, after a deletion and on failure.
  item: InboxItem | null;
  // The item was deleted elsewhere.
  gone: boolean;
  error: string | null;
  // Resolves true once deleted; a failure lands in `error`.
  remove: () => Promise<boolean>;
}

// One inbox item: read, marked read, and kept current over its own socket.
export function useInboxItem({ host, token, itemId, onAuthFailure }: InboxItemOptions): InboxItemState {
  const [item, setItem] = useState<InboxItem | null>(null);
  const [gone, setGone] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    setItem(null);
    setGone(false);
    setError(null);
    fetchInboxItem(host, token, itemId)
      .then((value) => {
        if (active) setItem((current) => fresherInboxItem(current, value));
      })
      .catch((reason) => {
        if (!active) return;
        if (isAuthError(reason)) {
          onAuthFailure();
          return;
        }
        setError(reason instanceof Error ? reason.message : "failed to load item");
      });
    markInboxRead(host, token, itemId).catch(() => {});
    return () => {
      active = false;
    };
  }, [host, token, itemId, onAuthFailure]);

  useEffect(() => {
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    function connect() {
      socket = connectInboxSocket(
        host,
        token,
        itemId,
        (message: SessionEnvelope) => {
          if (message.type !== "inbox_update") return;
          const payload = message.payload;
          if (payload.deleted) {
            setGone(true);
            setItem(null);
            return;
          }
          if (payload.item) setItem(payload.item as InboxItem);
        },
        () => {
          if (active) onAuthFailure();
        },
        {
          onOpen: () => {
            attempt = 0;
          },
          onClose: () => {
            if (!active) return;
            const delay = Math.min(15000, 500 * 2 ** attempt);
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }
    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [host, token, itemId, onAuthFailure]);

  const remove = useCallback(
    () =>
      deleteInboxItem(host, token, itemId)
        .then(() => true)
        .catch((reason) => {
          if (isAuthError(reason)) onAuthFailure();
          else setError(reason instanceof Error ? reason.message : "failed to delete");
          return false;
        }),
    [host, token, itemId, onAuthFailure],
  );

  return { item, gone, error, remove };
}
