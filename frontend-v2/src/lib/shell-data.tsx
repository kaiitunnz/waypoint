"use client";

import { createContext, useContext, useMemo, type ReactNode } from "react";

import { useInboxCount } from "@waypoint/web-core/hooks/useInboxCount";
import { useLaunchTargets, type LaunchTargetsState } from "@waypoint/web-core/hooks/useLaunchTargets";
import { usePendingAsks } from "@waypoint/web-core/hooks/usePendingAsks";
import type { ConnectionState } from "@waypoint/web-core/session/connection";
import { pendingAsksCount } from "@waypoint/web-core/pendingAsks";

import { handleAuthFailure } from "@/lib/auth";

export interface ShellData {
  host: string;
  token: string;
  // Pending asks plus open inbox items: everything waiting on the human.
  needsYou: number;
  connection: ConnectionState;
  launchTargets: LaunchTargetsState;
}

const ShellDataContext = createContext<ShellData | null>(null);

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
  const { snapshot, connection } = usePendingAsks(host, token, handleAuthFailure);
  const launchTargets = useLaunchTargets(host, token, handleAuthFailure);
  const needsYou = pendingAsksCount(snapshot) + inboxCount;

  const value = useMemo(
    () => ({ host, token, needsYou, connection, launchTargets }),
    [host, token, needsYou, connection, launchTargets],
  );
  return <ShellDataContext.Provider value={value}>{children}</ShellDataContext.Provider>;
}
