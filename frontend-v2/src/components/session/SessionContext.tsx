"use client";

import { createContext, useContext } from "react";

import type { AskQuestionEvidence } from "@waypoint/web-core/session/askQuestion";
import type { BackendModelOption, PendingQuestionsSnapshot } from "@waypoint/web-core/types";

export interface SessionContextValue {
  host: string;
  token: string;
  sessionId: string;
  // For labelling a model switch.
  modelOptions: BackendModelOption[];
}

const SessionContext = createContext<SessionContextValue | null>(null);

export const SessionContextProvider = SessionContext.Provider;

// What the cards of one session's transcript need: where its files and links live.
export function useSessionContext(): SessionContextValue {
  const value = useContext(SessionContext);
  if (!value) {
    throw new Error("useSessionContext needs a SessionContextProvider");
  }
  return value;
}

export interface AskState {
  evidence: AskQuestionEvidence;
  snapshot: PendingQuestionsSnapshot | null;
}

const AskStateContext = createContext<AskState | null>(null);

export const AskStateProvider = AskStateContext.Provider;

// How each question the transcript carries stands: answered, cancelled, ended or open.
export function useAskState(): AskState {
  const value = useContext(AskStateContext);
  if (!value) {
    throw new Error("useAskState needs an AskStateProvider");
  }
  return value;
}
