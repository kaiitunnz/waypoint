"use client";

import { createContext, useCallback, useContext, useMemo, type ReactNode } from "react";

import { useInboxCount } from "@waypoint/web-core/hooks/useInboxCount";
import { useLaunchTargets, type LaunchTargetsState } from "@waypoint/web-core/hooks/useLaunchTargets";
import { useSessionActions, type SessionActions } from "@waypoint/web-core/hooks/useSessionActions";
import { useSessionsFeed, type SessionsFeed } from "@waypoint/web-core/hooks/useSessionsFeed";
import type { ConnectionState } from "@waypoint/web-core/session/connection";
import { pendingAsksCount } from "@waypoint/web-core/pendingAsks";

import { handleAuthFailure } from "@/lib/auth";
import { useToast } from "@/components/ui/Toast";

export interface ShellData {
  host: string;
  token: string;
  // Pending asks plus open inbox items: everything waiting on the human.
  needsYou: number;
  connection: ConnectionState;
  launchTargets: LaunchTargetsState;
}

const ShellDataContext = createContext<ShellData | null>(null);

// The sessions feed and the row actions live apart from ShellData: the feed
// changes on every session event, and the navigation only needs the counts.
export interface SessionsData extends SessionsFeed {
  actions: SessionActions;
}

const SessionsDataContext = createContext<SessionsData | null>(null);

export function useSessionsData(): SessionsData {
  const value = useContext(SessionsDataContext);
  if (!value) {
    throw new Error("useSessionsData needs a ShellDataProvider");
  }
  return value;
}

export function useShellData(): ShellData {
  const value = useContext(ShellDataContext);
  if (!value) {
    throw new Error("useShellData needs a ShellDataProvider");
  }
  return value;
}

// Mounted once per signed-in host, so route changes keep its sockets and counts.
export function ShellDataProvider({
  host,
  token,
  children,
}: {
  host: string;
  token: string;
  children: ReactNode;
}) {
  const inboxCount = useInboxCount(host, token, handleAuthFailure);
  const feed = useSessionsFeed(host, token, handleAuthFailure);
  const launchTargets = useLaunchTargets(host, token, handleAuthFailure);
  const { toast } = useToast();
  const reportError = useCallback((message: string) => toast({ message, tone: "error" }), [toast]);
  const actions = useSessionActions(host, token, feed, reportError, handleAuthFailure);
  const needsYou = pendingAsksCount(feed.pendingAsks) + inboxCount;
  const { connection } = feed;

  const shell = useMemo(
    () => ({ host, token, needsYou, connection, launchTargets }),
    [host, token, needsYou, connection, launchTargets],
  );
  const sessions = useMemo(() => ({ ...feed, actions }), [feed, actions]);
  return (
    <ShellDataContext.Provider value={shell}>
      <SessionsDataContext.Provider value={sessions}>{children}</SessionsDataContext.Provider>
    </ShellDataContext.Provider>
  );
}
