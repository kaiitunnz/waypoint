"use client";

import { createContext, useContext } from "react";

import type { AskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import type { PendingQuestionsSnapshot } from "@waypoint/web-core/types";

interface SessionContextValue {
  host: string;
  token: string;
  sessionId: string;
  // The session's working directory: paths inside it read relative to it.
  cwd: string;
  // Who the agent's messages are from, for assistive technology.
  agentName: string;
  // Full density opens runs by themselves.
  full: boolean;
  // Structured transports have cards; the rest show raw events.
  structured: boolean;
}

const SessionContext = createContext<SessionContextValue | null>(null);

export const SessionContextProvider = SessionContext.Provider;

export function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSessionContext needs a SessionContextProvider");
  }
  return value;
}

interface AskState {
  evidence: AskQuestionEvidence;
  snapshot: PendingQuestionsSnapshot | null;
}

const AskStateContext = createContext<AskState | null>(null);

export const AskStateProvider = AskStateContext.Provider;

export function useAskState(): AskState {
  const value = useContext(AskStateContext);
  if (!value) {
    throw new Error("useAskState needs an AskStateProvider");
  }
  return value;
}
