"use client";

import { createContext, useContext } from "react";

// Lets a transcript card open the session Files browser without prop-drilling
// through the transcript tree. Mirrors WorkspaceFileLinkContext.
export interface SessionFilesLinkHandler {
  openFilesBrowser: () => void;
}

const SessionFilesLinkContext = createContext<SessionFilesLinkHandler | null>(null);

export const SessionFilesLinkProvider = SessionFilesLinkContext.Provider;

export function useSessionFilesLink(): SessionFilesLinkHandler | null {
  return useContext(SessionFilesLinkContext);
}
