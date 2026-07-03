"use client";

import Link from "next/link";
import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";

import { connectSessionsSocket, fetchInboxUnresolvedCount } from "@/lib/api";
import { readHost, readToken, TOKEN_EVENT } from "@/lib/store";
import type { SessionEnvelope } from "@/lib/types";

const InboxCountContext = createContext<number>(0);

export function useInboxCount(): number {
  return useContext(InboxCountContext);
}

// App-wide unresolved-inbox count. Seeds from the REST count, then tracks the
// absolute `unresolved_count` carried on every `inbox_update` over the global
// session socket, re-fetching on (re)connect to close the reconnect gap. Renders
// the bottom-left inbox floater on every route (only when authenticated).
export function InboxCountProvider({ children }: { children: ReactNode }) {
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [count, setCount] = useState(0);

  useEffect(() => {
    const sync = () => {
      setHost(readHost());
      setToken(readToken());
    };
    sync();
    window.addEventListener(TOKEN_EVENT, sync);
    window.addEventListener("storage", sync);
    return () => {
      window.removeEventListener(TOKEN_EVENT, sync);
      window.removeEventListener("storage", sync);
    };
  }, []);

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
    <InboxCountContext.Provider value={count}>
      {children}
      {token ? <InboxDock count={count} /> : null}
    </InboxCountContext.Provider>
  );
}

function InboxDock({ count }: { count: number }) {
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
