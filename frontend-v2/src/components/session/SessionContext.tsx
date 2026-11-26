"use client";

import { createContext, useContext } from "react";

import type { AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";

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
  // Opens the session's files, or null where the session has none to show.
  openFiles: (() => void) | null;
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

// One controller per session: the dock and the transcript card share its drafts and requests.
const AskControllerContext = createContext<AskQuestionController | null>(null);

export const AskControllerProvider = AskControllerContext.Provider;

export function useAskController(): AskQuestionController {
  const controller = useContext(AskControllerContext);
  if (!controller) throw new Error("useAskController needs an AskControllerProvider");
  return controller;
}

// Keeps the transcript's view still when something in it changes size without an
// aria-expanded control. Call it before the change.
const TranscriptHoldContext = createContext<(element: Element, closing: boolean) => void>(() => {});

export const TranscriptHoldProvider = TranscriptHoldContext.Provider;

export function useTranscriptHold() {
  return useContext(TranscriptHoldContext);
}
