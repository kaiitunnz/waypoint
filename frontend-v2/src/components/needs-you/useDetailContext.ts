"use client";

import { useMemo } from "react";

import { useShellData } from "@/lib/shell-data";

// What a detail renders transcript content in: no session view to open files in.
export function useDetailContext(sessionId: string, cwd: string, agentName: string) {
  const { host, token } = useShellData();
  return useMemo(
    () => ({ host, token, sessionId, cwd, agentName, full: false, structured: true, openFiles: null }),
    [host, token, sessionId, cwd, agentName],
  );
}
