"use client";

import { useMemo } from "react";

import type { BoardGate } from "@waypoint/web-core/board";
import { inboxListKey, useInboxList } from "@waypoint/web-core/hooks/useInboxList";
import { updatedToday, type InboxStatusFilter } from "@waypoint/web-core/inbox";
import { buildQueue, type SourceFilter } from "@waypoint/web-core/needsYou";
import type { InboxItem, PendingAsk } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { useSessionsData, useShellData } from "@/lib/shell-data";

const NO_ASKS: PendingAsk[] = [];
const NO_GATES: BoardGate[] = [];
const NO_ITEMS: InboxItem[] = [];

// The queue for a status, source and search, and what was resolved today.
export function useQueue(status: InboxStatusFilter, source: SourceFilter, q: string, now: number) {
  const { host, token, waiting, gates: loadedGates, gatesFailed, retryGates } = useShellData();
  const { pendingAsks, subscribeInbox } = useSessionsData();
  const asks = pendingAsks?.items ?? NO_ASKS;
  const gates = loadedGates ?? NO_GATES;

  const list = useInboxList({
    host,
    token,
    status,
    q,
    onAuthFailure: handleAuthFailure,
    subscribe: subscribeInbox,
  });
  const resolved = useInboxList({
    host,
    token,
    status: "resolved",
    q: "",
    onAuthFailure: handleAuthFailure,
    subscribe: subscribeInbox,
    enabled: status === "open",
  });

  // Asks and gates arrive apart from the items: until they have, an empty queue
  // may not be one. A failed gate read is reported rather than waited for.
  const listsGates = status !== "resolved" && (source === "all" || source === "board");
  const boardFailed = listsGates && gatesFailed;
  const awaiting =
    (status !== "resolved" && (source === "all" || source === "sessions") && !pendingAsks) ||
    (listsGates && !loadedGates && !gatesFailed);

  // Items of an earlier status or search are not shown while the next ones load.
  const loaded = list.loadedFor === inboxListKey(status, list.query);
  const items = loaded ? list.items : NO_ITEMS;
  const entries = useMemo(
    () => buildQueue({ asks, items, gates, status, source, q: list.query }),
    [asks, items, gates, status, source, list.query],
  );

  const showsToday = status === "open" && !list.query.trim() && (source === "all" || source === "inbox");
  const todayEntries = useMemo(() => {
    if (!showsToday) return [];
    const resolvedToday = updatedToday(resolved.items, now);
    // The gates keep their items; what they hide stays hidden here too.
    return buildQueue({ asks: [], items: resolvedToday, gates, status: "all", source: "inbox", q: "" });
  }, [showsToday, resolved.items, gates, now]);
  // More than the loaded page when all of it is from today.
  const todayMore = resolved.hasMore && updatedToday(resolved.items, now).length === resolved.items.length;

  return {
    entries,
    todayEntries,
    todayMore,
    counts: waiting,
    awaiting,
    boardFailed,
    retryGates,
    loaded,
    list,
    resolved,
  };
}
