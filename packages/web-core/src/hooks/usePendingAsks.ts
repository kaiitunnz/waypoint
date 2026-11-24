"use client";

import { useEffect, useState } from "react";

import { connectSessionsSocket, fetchPendingAsks, isAuthError } from "../api";
import { applyPendingAsksSnapshot, parsePendingAsksSnapshot } from "../pendingAsks";
import type { PendingAsksSnapshot, SessionEnvelope } from "../types";

export type SessionsConnection = "connecting" | "open" | "reconnecting";

export interface PendingAsksState {
  snapshot: PendingAsksSnapshot | null;
  connection: SessionsConnection;
}

// Pending asks across every live session: seeds from GET /api/pending-asks, then
// follows `pending_asks` frames on /ws/sessions (docs/pending_asks.md), keeping
// a snapshot only when its epoch differs or its revision is higher. Re-fetches
// on every (re)connect to close the gap while the socket was down.
// `onAuthFailure` must be stable (it restarts the socket).
export function usePendingAsks(
  host: string,
  token: string,
  onAuthFailure?: () => void,
): PendingAsksState {
  const [snapshot, setSnapshot] = useState<PendingAsksSnapshot | null>(null);
  const [connection, setConnection] = useState<SessionsConnection>("connecting");

  useEffect(() => {
    if (!host || !token) {
      return;
    }
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;
    let fetchAbort: AbortController | null = null;

    const apply = (incoming: PendingAsksSnapshot) => {
      if (active) setSnapshot((held) => applyPendingAsksSnapshot(held, incoming));
    };
    const failAuth = () => {
      if (active) onAuthFailure?.();
    };
    const refresh = () => {
      fetchAbort?.abort();
      const controller = new AbortController();
      fetchAbort = controller;
      fetchPendingAsks(host, token, controller.signal)
        .then(apply)
        .catch((error) => {
          if (isAuthError(error)) failAuth();
        });
    };

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type === "auth_revoked") {
            failAuth();
            return;
          }
          if (message.type !== "pending_asks") return;
          const parsed = parsePendingAsksSnapshot(message.payload);
          if (parsed) apply(parsed);
        },
        failAuth,
        {
          onOpen: () => {
            attempt = 0;
            if (active) setConnection("open");
            refresh();
          },
          onClose: () => {
            if (!active) return;
            setConnection("reconnecting");
            const delay = Math.min(15000, 500 * 2 ** attempt);
            attempt += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }

    refresh();
    connect();

    return () => {
      active = false;
      fetchAbort?.abort();
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
      setSnapshot(null);
      setConnection("connecting");
    };
  }, [host, token, onAuthFailure]);

  return { snapshot, connection };
}
