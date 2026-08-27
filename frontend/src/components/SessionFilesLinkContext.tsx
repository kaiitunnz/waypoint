"use client";

import { createContext, useContext } from "react";

// Lets transcript-deep cards open the session Files browser, which is owned by
// the composer. Mirrors WorkspaceFileLinkContext so a card doesn't prop-drill
// through the transcript tree.
export interface SessionFilesLinkHandler {
  openFilesBrowser: () => void;
}

const SessionFilesLinkContext = createContext<SessionFilesLinkHandler | null>(null);

export const SessionFilesLinkProvider = SessionFilesLinkContext.Provider;

export function useSessionFilesLink(): SessionFilesLinkHandler | null {
  return useContext(SessionFilesLinkContext);
}
