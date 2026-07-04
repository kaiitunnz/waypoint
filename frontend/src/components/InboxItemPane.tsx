"use client";

import { useCallback, useEffect, useState } from "react";

import { InboxItemView } from "@/components/InboxItemView";
import {
  connectInboxSocket,
  deleteInboxItem,
  fetchInboxItem,
  isAuthError,
  markInboxRead,
} from "@/lib/api";
import type { InboxItem, SessionEnvelope } from "@/lib/types";

// Smart wrapper for a single inbox item: hydrates it, marks it read, keeps it
// live over /ws/inbox/{id}, and owns delete. Shared by the desktop split-view
// right pane and the mobile full-page route.
export function InboxItemPane({
  host,
  token,
  itemId,
  onAuthFailure,
  onDeleted,
}: {
  host: string;
  token: string;
  itemId: string;
  onAuthFailure: () => void;
  onDeleted: () => void;
}) {
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
        if (active) setItem(value);
      })
      .catch((err) => {
        if (!active) return;
        if (isAuthError(err)) {
          onAuthFailure();
          return;
        }
        setError(err instanceof Error ? err.message : "failed to load item");
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

  const handleDelete = useCallback(() => {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Delete this inbox item? This cannot be undone.")
    ) {
      return;
    }
    deleteInboxItem(host, token, itemId)
      .then(() => onDeleted())
      .catch((err) => {
        if (isAuthError(err)) onAuthFailure();
        else setError(err instanceof Error ? err.message : "failed to delete");
      });
  }, [host, token, itemId, onAuthFailure, onDeleted]);

  if (gone) {
    return <div className="inbox-empty">This item was deleted.</div>;
  }
  if (error) {
    return <div className="inbox-empty">{error}</div>;
  }
  if (!item) {
    return <div className="inbox-empty">Loading…</div>;
  }
  return (
    <InboxItemView
      host={host}
      token={token}
      item={item}
      onDelete={handleDelete}
    />
  );
}
