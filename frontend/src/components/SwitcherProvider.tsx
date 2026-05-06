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

import { clearToken, readHost, readToken } from "@/lib/store";
import { SessionRecord } from "@/lib/types";

import { SessionSwitcher } from "./SessionSwitcher";

interface SwitcherContextValue {
  openSwitcher: () => void;
  setCurrentSession: (session: SessionRecord | null) => void;
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
    setToken("");
    setOpen(false);
    router.replace("/");
  }, [router]);

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

  const value = useMemo(
    () => ({ openSwitcher, setCurrentSession }),
    [openSwitcher],
  );

  return (
    <SwitcherContext.Provider value={value}>
      {children}
      {open && host && token ? (
        <SessionSwitcher
          host={host}
          token={token}
          currentSession={currentSession}
          onAuthFailure={handleAuthFailure}
          onClose={closeSwitcher}
        />
      ) : null}
    </SwitcherContext.Provider>
  );
}
