"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { connectSessionsSocket, fetchInboxUnresolvedCount } from "@/lib/api";
import type { SessionEnvelope } from "@/lib/types";

// Bottom-left glass floater with the unresolved-inbox count. Mounted only on
// the homepage (auth is owned there and passed in), so the count socket lives
// only while the homepage is mounted. Seeds from the REST count, then tracks
// the absolute `unresolved_count` on every `inbox_update` over the global
// session socket, re-fetching on (re)connect to close the reconnect gap.
export function InboxDock({ host, token }: { host: string; token: string }) {
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

  return (
    <Link className="inbox-dock" href="/inbox" aria-label="Open inbox">
      <span className="inbox-dock-glyph" aria-hidden="true">
        <EnvelopeIcon />
      </span>
      <span className="inbox-dock-label">Inbox</span>
      {count > 0 ? (
        <span className="inbox-dock-count">{count > 99 ? "99+" : count}</span>
      ) : null}
    </Link>
  );
}

function EnvelopeIcon() {
  return (
    <svg
      viewBox="0 0 24 24"
      width="18"
      height="18"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <rect x="3" y="5" width="18" height="14" rx="2" />
      <path d="m3 7 9 6 9-6" />
    </svg>
  );
}
