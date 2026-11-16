"use client";

import { useRouter } from "next/navigation";
import {
  createContext,
  ReactNode,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { connectSessionsSocket, parseLiveSessionList } from "@/lib/api";
import { useBackendCatalog } from "@/lib/backends";
import {
  applySessionState,
  clearSessionHistory,
  upsertLiveSession,
} from "@/lib/sessionHistory";
import { clearToken, readHost, readToken } from "@/lib/store";
import { SessionEnvelope, SessionRecord } from "@/lib/types";

import { SessionSwitcher } from "./SessionSwitcher";

interface SwitcherContextValue {
  openSwitcher: () => void;
  setCurrentSession: (session: SessionRecord | null) => void;
}

// The switcher's live list outlives the modal so reopening shows it at once;
// it is keyed by host so switching backends never shows another host's rows.
interface LiveCache {
  host: string;
  sessions: SessionRecord[];
  historyCount: number;
  historyRevision: string;
}

const SwitcherContext = createContext<SwitcherContextValue | null>(null);

export function useSwitcher(): SwitcherContextValue {
  const ctx = useContext(SwitcherContext);
  if (!ctx) {
    throw new Error("useSwitcher must be used within SwitcherProvider");
  }
  return ctx;
}

export function SwitcherProvider({ children }: { children: ReactNode }) {
  const router = useRouter();
  const [host, setHost] = useState("");
  const [token, setToken] = useState("");
  const [open, setOpen] = useState(false);
  const [currentSession, setCurrentSession] = useState<SessionRecord | null>(null);
  const [live, setLive] = useState<LiveCache | null>(null);
  const [syncing, setSyncing] = useState(false);
  const catalog = useBackendCatalog(host || null, token || null, null);

  // Re-reading on every open keeps credentials fresh after login on
  // another route — RootLayout mounts once for the whole app session.
  const refreshCreds = useCallback(() => {
    setHost(readHost());
    setToken(readToken());
  }, []);

  const openSwitcher = useCallback(() => {
    refreshCreds();
    setOpen(true);
  }, [refreshCreds]);

  const closeSwitcher = useCallback(() => setOpen(false), []);

  const handleAuthFailure = useCallback(() => {
    clearToken();
    clearSessionHistory(host);
    setToken("");
    setLive(null);
    setOpen(false);
    router.replace("/");
  }, [host, router]);

  useEffect(() => {
    function handleKey(event: globalThis.KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        refreshCreds();
        setOpen((current) => !current);
      }
    }
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [refreshCreds]);

  // The socket lives only while the modal is open: its first frame refreshes
  // the cached list, and later frames keep it current until close.
  useEffect(() => {
    if (!open || !host || !token) return;
    let active = true;
    setSyncing(true);
    const socket = connectSessionsSocket(
      host,
      token,
      (message: SessionEnvelope) => {
        if (!active) return;
        if (message.type === "session_list_update") {
          setLive({ host, ...parseLiveSessionList(message.payload) });
          setSyncing(false);
          return;
        }
        if (message.type === "session_state") {
          const updated = message.payload.session as SessionRecord;
          applySessionState(host, updated);
          setLive((current) =>
            current && current.host === host
              ? { ...current, sessions: upsertLiveSession(current.sessions, updated) }
              : current,
          );
        }
      },
      () => {
        if (active) handleAuthFailure();
      },
      {
        onClose: () => {
          if (active) setSyncing(false);
        },
      },
    );
    return () => {
      active = false;
      setSyncing(false);
      socket.close();
    };
  }, [open, host, token, handleAuthFailure]);

  const value = useMemo(
    () => ({ openSwitcher, setCurrentSession }),
    [openSwitcher],
  );

  const cached = live && live.host === host ? live : null;

  return (
    <SwitcherContext.Provider value={value}>
      {children}
      {open && host && token ? (
        <SessionSwitcher
          host={host}
          token={token}
          catalog={catalog}
          liveSessions={cached?.sessions ?? null}
          historyCount={cached?.historyCount ?? 0}
          historyRevision={cached?.historyRevision ?? ""}
          syncing={syncing}
          currentSession={currentSession}
          onAuthFailure={handleAuthFailure}
          onClose={closeSwitcher}
        />
      ) : null}
    </SwitcherContext.Provider>
  );
}
