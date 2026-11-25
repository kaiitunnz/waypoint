"use client";

import { useState } from "react";

import { useSessionHistory, useMergedSessionPage } from "@waypoint/web-core/sessionHistory";
import { HOME_HIDDEN_SOURCES } from "@waypoint/web-core/sessionSections";
import type { SessionRecord } from "@waypoint/web-core/types";

import { handleAuthFailure } from "@/lib/auth";
import { useSessionsData, useShellData } from "@/lib/shell-data";

export const EXITED_PAGE_SIZE = 10;

// What a list needs to show the rest of its exited sessions.
export interface ExitedPaging {
  loading: boolean;
  failed: boolean;
  onRetry: () => void;
  hasMore: boolean;
  onMore: () => void;
}

interface ExitedOptions {
  // The query typed, or "" for the plain Exited group.
  query: string;
  // The exited sessions the live list still carries.
  live: SessionRecord[];
  // Rows that lead the page, such as pinned search matches.
  lead?: SessionRecord[];
  // Whether to load the rows; the count is always kept.
  enabled: boolean;
}

// Exited sessions: the ones the live list carries and the history the server pages,
// with a count that stays fresh while the rows are not loaded.
export function useExitedSessions({ query, live, lead, enabled }: ExitedOptions) {
  const { host, token } = useShellData();
  // A new query starts from the first page again.
  const [paged, setPaged] = useState({ query, limit: EXITED_PAGE_SIZE });
  if (paged.query !== query) setPaged({ query, limit: EXITED_PAGE_SIZE });
  const limit = paged.query === query ? paged.limit : EXITED_PAGE_SIZE;
  const feed = useSessionsData();
  const common = {
    host,
    token,
    revision: feed.historyRevision,
    excludeSources: HOME_HIDDEN_SOURCES,
    onAuthError: handleAuthFailure,
  };
  const counted = useSessionHistory({ ...common, query: "", withItems: false });
  const history = useSessionHistory({ ...common, query, withItems: true, enabled, pageSize: EXITED_PAGE_SIZE });
  const listed = useMergedSessionPage(live, history, true, 1, limit, lead);
  const paging: ExitedPaging = {
    loading: history.pending || listed.filling,
    failed: Boolean(history.error),
    onRetry: history.retry,
    hasMore: listed.items.length < listed.total,
    onMore: () => setPaged({ query, limit: limit + EXITED_PAGE_SIZE }),
  };
  return { count: live.length + (counted.total ?? 0), listed, paging };
}
