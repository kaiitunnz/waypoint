"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import type { BoardGate } from "@waypoint/web-core/board";
import { useBoardGates } from "@waypoint/web-core/hooks/useBoardGates";
import { useInboxCount } from "@waypoint/web-core/hooks/useInboxCount";
import { useLaunchTargets, type LaunchTargetsState } from "@waypoint/web-core/hooks/useLaunchTargets";
import { useSessionsFeed, type SessionsFeed } from "@waypoint/web-core/hooks/useSessionsFeed";
import type { ConnectionState } from "@waypoint/web-core/session/connection";
import { needsCounts, type NeedsCounts } from "@waypoint/web-core/needsYou";
import { pendingAsksCount } from "@waypoint/web-core/pendingAsks";
import { visibleSessions } from "@waypoint/web-core/sessionSections";
import type { SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";

export interface ShellData {
  host: string;
  token: string;
  // Pending asks, open inbox items and board gates: everything waiting on the human.
  needsYou: number;
  waiting: NeedsCounts;
  // Null until first read.
  gates: BoardGate[] | null;
  connection: ConnectionState;
  launchTargets: LaunchTargetsState;
}

const ShellDataContext = createContext<ShellData | null>(null);

// The feed changes on every session event, so it lives apart from ShellData, which
// the navigation reads for its counts.
export interface SessionsData extends SessionsFeed {
  // The sessions the home and the switcher list.
  visible: SessionRecord[];
  askSessionIds: ReadonlySet<string>;
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
  const gates = useBoardGates(host, token, feed.boardRevision + feed.inboxRevision, handleAuthFailure);
  const asks = pendingAsksCount(feed.pendingAsks);
  const waiting = useMemo(() => needsCounts(asks, inboxCount, gates ?? []), [asks, inboxCount, gates]);
  const needsYou = waiting.all;
  const { connection } = feed;

  const shell = useMemo(
    () => ({ host, token, needsYou, waiting, gates, connection, launchTargets }),
    [host, token, needsYou, waiting, gates, connection, launchTargets],
  );
  const sessions = useMemo<SessionsData>(
    () => ({
      ...feed,
      visible: visibleSessions(feed.sessions ?? []),
      askSessionIds: new Set((feed.pendingAsks?.items ?? []).map((ask) => ask.session_id)),
    }),
    [feed],
  );
  return (
    <ShellDataContext.Provider value={shell}>
      <SessionsDataContext.Provider value={sessions}>{children}</SessionsDataContext.Provider>
    </ShellDataContext.Provider>
  );
}
