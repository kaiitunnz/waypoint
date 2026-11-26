"use client";

import { readsDialogsFromScreen } from "@waypoint/web-core/backends";
import { useAskCall as useSharedAskCall } from "@waypoint/web-core/hooks/useApprovalRequest";
import type { AskCall } from "@waypoint/web-core/pendingAskActions";
import type { AskSession } from "@waypoint/web-core/session/wholeRequest";
import type { PendingAsk } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { useSessionsData, useShellData } from "@/lib/shell-data";

// What reading an ask's request needs to know of its session.
export function useAskSession(sessionId: string): AskSession {
  const { catalog } = useShellData();
  const { sessions } = useSessionsData();
  const session = sessions?.find((entry) => entry.id === sessionId);
  return {
    cwd: session?.cwd ?? "",
    fromScreen: readsDialogsFromScreen(session?.transport, catalog),
    revision: session?.last_event_at ?? "",
  };
}

// The call behind an ask, as the card or row that lists it needs it.
export function useAskCall(ask: PendingAsk | null): AskCall {
  const { host, token } = useShellData();
  const session = useAskSession(ask?.session_id ?? "");
  return useSharedAskCall(host, token, ask, session, handleAuthFailure);
}
