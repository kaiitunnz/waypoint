"use client";

import { createContext, useContext } from "react";

import type { AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";

// One controller per session: the dock and the transcript card share its drafts and requests.
const AskControllerContext = createContext<AskQuestionController | null>(null);

export const AskControllerProvider = AskControllerContext.Provider;

export function useAskController(): AskQuestionController {
  const controller = useContext(AskControllerContext);
  if (!controller) throw new Error("useAskController needs an AskControllerProvider");
  return controller;
}
