"use client";

import { useCallback } from "react";

import { readsDialogsFromScreen } from "@waypoint/web-core/backends";
import type { AskSession } from "@waypoint/web-core/session/wholeRequest";

import { useSessionsData, useShellData } from "@/lib/shell-data";

// What a pending ask's surfaces need to know of the session it comes from.
export function useAskSessions(): (sessionId: string) => AskSession {
  const { catalog } = useShellData();
  const { sessions } = useSessionsData();
  return useCallback(
    (sessionId: string) => {
      const session = sessions?.find((entry) => entry.id === sessionId);
      return { cwd: session?.cwd ?? "", fromScreen: readsDialogsFromScreen(session?.transport, catalog) };
    },
    [sessions, catalog],
  );
}
