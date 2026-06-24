"use client";

import { createContext, useContext } from "react";

export interface WorkspaceLinkHandler {
  // `fromBareText` marks a link the transcript synthesized from a bare path in
  // prose (vs. an explicit markdown link). It controls the fallback when the
  // path can't be resolved: explicit links open in a new tab, synthesized ones
  // stay inert so a false-positive match never spawns a junk navigation.
  openWorkspacePath: (href: string, opts?: { fromBareText?: boolean }) => void;
}

const WorkspaceFileLinkContext = createContext<WorkspaceLinkHandler | null>(null);

export const WorkspaceFileLinkProvider = WorkspaceFileLinkContext.Provider;

export function useWorkspaceFileLink(): WorkspaceLinkHandler | null {
  return useContext(WorkspaceFileLinkContext);
}
