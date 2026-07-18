"use client";

import { useEffect, useState } from "react";

import { connectSessionsSocket, fetchInboxUnresolvedCount } from "@/lib/api";
import type { SessionEnvelope } from "@/lib/types";

// Shared unresolved-inbox count for the nav count and the floater badge. Seeds
// from the REST count, then tracks the absolute `unresolved_count` on every
// `inbox_update` over a dedicated session socket, re-fetching on (re)connect to
// close the reconnect gap.
export function useInboxCount(host: string, token: string): number {
  const [count, setCount] = useState(0);

  useEffect(() => {
    if (!host || !token) {
      setCount(0);
      return;
    }
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let attempt = 0;

    const refresh = () => {
      fetchInboxUnresolvedCount(host, token)
        .then((value) => {
          if (active) setCount(value);
        })
        .catch(() => {});
    };
    refresh();

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type !== "inbox_update") return;
          const value = message.payload.unresolved_count;
          if (typeof value === "number") setCount(value);
        },
        () => {
          if (active) setCount(0);
        },
        {
          onOpen: () => {
            attempt = 0;
            refresh();
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
  }, [host, token]);

  return count;
}
