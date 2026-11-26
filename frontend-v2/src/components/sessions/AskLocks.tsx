"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from "react";

import type { AskPhase } from "./useAskResponder";

// An answered ask leaves the feed when the agent closes it; if it is still listed
// after this, it takes input again.
export const SENT_HOLD_MS = 10_000;

export interface AskLocks {
  phase: (id: string) => AskPhase;
  setPhase: (id: string, phase: AskPhase) => void;
}

const IDLE: AskPhase = { state: "idle" };

const AskLocksContext = createContext<AskLocks | null>(null);

export const useAskLocks = () => useContext(AskLocksContext);

// One reply at a time per ask, however many surfaces show it: a row and the open
// detail share its phase. Ask ids name one request, so a phase is never reused.
export function AskLocksProvider({ children }: { children: ReactNode }) {
  const [phases, setPhases] = useState<Record<string, AskPhase>>({});
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>());

  const clearTimer = useCallback((id: string) => {
    clearTimeout(timers.current.get(id));
    timers.current.delete(id);
  }, []);

  const setPhase = useCallback(
    (id: string, phase: AskPhase) => {
      clearTimer(id);
      setPhases((current) => ({ ...current, [id]: phase }));
      if (phase.state === "sent") {
        timers.current.set(
          id,
          setTimeout(() => setPhases((current) => ({ ...current, [id]: IDLE })), SENT_HOLD_MS),
        );
      }
    },
    [clearTimer],
  );

  useEffect(() => {
    const pending = timers.current;
    return () => pending.forEach((timer) => clearTimeout(timer));
  }, []);

  const value = useMemo<AskLocks>(() => ({ phase: (id) => phases[id] ?? IDLE, setPhase }), [phases, setPhase]);
  return <AskLocksContext.Provider value={value}>{children}</AskLocksContext.Provider>;
}
