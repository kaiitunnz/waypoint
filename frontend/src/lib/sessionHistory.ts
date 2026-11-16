"use client";

import { useCallback, useEffect, useMemo, useRef, useState, useSyncExternalStore } from "react";

import { AuthError, fetchSessionHistory } from "@/lib/api";
import { matchesQuery, parseQuery } from "@/lib/search";
import { SessionRecord } from "@/lib/types";

// Exited, unpinned sessions ("history") are paged from the server; live and
// pinned sessions arrive over the sessions socket. This module caches history
// per (host, query, excluded sources) for the app's lifetime so surfaces open
// instantly from the last result and revalidate in the background.

export const SESSION_SEARCH_FIELDS = [
  "title",
  "cwd",
  "repo_name",
  "branch",
  "backend",
  "search_status",
];

const MAX_PAGE_SIZE = 100;
const QUERY_DEBOUNCE_MS = 200;
const REVISION_DEBOUNCE_MS = 300;
const NO_SOURCES: readonly string[] = [];

interface HistoryEntry {
  host: string;
  query: string;
  excludeSources: readonly string[];
  withItems: boolean;
  // null until items are first fetched; count-only entries never fetch them.
  items: SessionRecord[] | null;
  cursor: string | null;
  hasMore: boolean;
  total: number | null;
  revision: string | null;
  stale: boolean;
  loading: boolean;
  error: string | null;
}

const entries = new Map<string, HistoryEntry>();
const listeners = new Set<() => void>();
const inflight = new Map<string, AbortController>();

function emit(): void {
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function patch(key: string, changes: Partial<HistoryEntry>): void {
  const current = entries.get(key);
  if (!current) return;
  entries.set(key, { ...current, ...changes });
  emit();
}

function entryKey(
  host: string,
  query: string,
  excludeSources: readonly string[],
  withItems: boolean,
): string {
  return JSON.stringify([host, query, [...excludeSources].sort(), withItems]);
}

export function isHistorySession(session: SessionRecord): boolean {
  return session.status === "exited" && !session.pinned_at;
}

/** Upsert a session into a live list, dropping it once it becomes history. */
export function upsertLiveSession(
  sessions: SessionRecord[],
  updated: SessionRecord,
): SessionRecord[] {
  const index = sessions.findIndex((session) => session.id === updated.id);
  if (isHistorySession(updated)) {
    return index === -1 ? sessions : sessions.filter((session) => session.id !== updated.id);
  }
  if (index === -1) return [updated, ...sessions];
  return sessions.map((session) => (session.id === updated.id ? updated : session));
}

export function byRecency(a: SessionRecord, b: SessionRecord): number {
  return b.last_event_at.localeCompare(a.last_event_at) || b.id.localeCompare(a.id);
}

function appendUnique(items: SessionRecord[], more: SessionRecord[]): SessionRecord[] {
  const seen = new Set(items.map((session) => session.id));
  return [...items, ...more.filter((session) => !seen.has(session.id))];
}

function belongsTo(entry: HistoryEntry, session: SessionRecord): boolean {
  return (
    isHistorySession(session) &&
    !entry.excludeSources.includes(session.source) &&
    matchesQuery(session, parseQuery(entry.query), SESSION_SEARCH_FIELDS)
  );
}

function reconcile(entry: HistoryEntry, session: SessionRecord): HistoryEntry {
  const belongs = belongsTo(entry, session);
  const items = entry.items;
  const present = items?.some((item) => item.id === session.id) ?? false;
  if (present && items) {
    const rest = items.filter((item) => item.id !== session.id);
    if (!belongs) {
      return { ...entry, items: rest, total: entry.total === null ? null : Math.max(0, entry.total - 1) };
    }
    return { ...entry, items: [...rest, session].sort(byRecency) };
  }
  if (!belongs) return entry;
  // A session that just exited carries the newest activity, so it lands inside
  // the loaded window; anything older is left for the next revalidation so the
  // loaded prefix stays correctly ordered.
  const oldest = items && items.length > 0 ? items[items.length - 1].last_event_at : null;
  const inWindow = items !== null && (!entry.hasMore || (oldest !== null && session.last_event_at >= oldest));
  if (items !== null && !inWindow) return entry;
  return {
    ...entry,
    items: items === null ? null : [...items, session].sort(byRecency),
    total: entry.total === null ? null : entry.total + 1,
  };
}

/** Fold a `session_state` frame into every cached history view for `host`. */
export function applySessionState(host: string, session: SessionRecord): void {
  let changed = false;
  for (const [key, entry] of entries) {
    if (entry.host !== host) continue;
    const next = reconcile(entry, session);
    if (next !== entry) {
      entries.set(key, next);
      changed = true;
    }
  }
  if (changed) emit();
}

/** Drop a deleted session from every cached history view for `host`. */
export function forgetHistorySession(host: string, sessionId: string): void {
  let changed = false;
  for (const [key, entry] of entries) {
    if (entry.host !== host || !entry.items?.some((item) => item.id === sessionId)) continue;
    entries.set(key, {
      ...entry,
      items: entry.items.filter((item) => item.id !== sessionId),
      total: entry.total === null ? null : Math.max(0, entry.total - 1),
    });
    changed = true;
  }
  if (changed) emit();
}

/** Mark every cached history view for `host` for revalidation. */
export function invalidateSessionHistory(host: string): void {
  let changed = false;
  for (const [key, entry] of entries) {
    if (entry.host !== host) continue;
    entries.set(key, { ...entry, stale: true });
    changed = true;
  }
  if (changed) emit();
}

/** Forget every cached history view for `host` (logout, auth failure). */
export function clearSessionHistory(host: string): void {
  for (const [key, entry] of entries) {
    if (entry.host !== host) continue;
    inflight.get(key)?.abort();
    inflight.delete(key);
    entries.delete(key);
  }
  emit();
}

function begin(key: string): AbortController {
  inflight.get(key)?.abort();
  const controller = new AbortController();
  inflight.set(key, controller);
  return controller;
}

function isCurrent(key: string, controller: AbortController): boolean {
  return inflight.get(key) === controller && !controller.signal.aborted;
}

async function settle(
  key: string,
  controller: AbortController,
  run: () => Promise<Partial<HistoryEntry>>,
  onAuthError?: () => void,
): Promise<void> {
  patch(key, { loading: true, error: null });
  try {
    const changes = await run();
    if (isCurrent(key, controller)) patch(key, { ...changes, loading: false });
  } catch (error) {
    if (!isCurrent(key, controller)) return;
    if (error instanceof AuthError) onAuthError?.();
    patch(key, {
      loading: false,
      error: error instanceof Error ? error.message : "failed to load session history",
    });
  } finally {
    if (inflight.get(key) === controller) inflight.delete(key);
  }
}

// Refetch from the top, as many rows as are loaded, and swap atomically so a
// row deleted, revived, or pinned elsewhere never lingers in a later page.
function revalidate(
  key: string,
  token: string,
  revision: string,
  pageSize: number,
  onAuthError?: () => void,
): Promise<void> {
  const entry = entries.get(key);
  if (!entry) return Promise.resolve();
  const controller = begin(key);
  const { host, query, excludeSources, withItems } = entry;
  return settle(
    key,
    controller,
    async () => {
      if (!withItems) {
        const page = await fetchSessionHistory(host, token, {
          query,
          limit: 0,
          excludeSources,
          signal: controller.signal,
        });
        return { total: page.total, revision, stale: false };
      }
      const want = Math.max(pageSize, entry.items?.length ?? 0);
      let items: SessionRecord[] = [];
      let cursor: string | null = null;
      let hasMore = true;
      let total: number | null = null;
      while (hasMore && items.length < want) {
        const page = await fetchSessionHistory(host, token, {
          query,
          cursor,
          limit: Math.min(MAX_PAGE_SIZE, want - items.length),
          excludeSources,
          signal: controller.signal,
        });
        items = appendUnique(items, page.sessions);
        cursor = page.cursor;
        hasMore = page.has_more;
        total = page.total;
      }
      return { items, cursor, hasMore, total, revision, stale: false };
    },
    onAuthError,
  );
}

function loadMore(key: string, token: string, pageSize: number, onAuthError?: () => void): void {
  const entry = entries.get(key);
  if (!entry || !entry.items || !entry.hasMore || entry.loading) return;
  const controller = begin(key);
  const { host, query, excludeSources, cursor, items } = entry;
  void settle(
    key,
    controller,
    async () => {
      const page = await fetchSessionHistory(host, token, {
        query,
        cursor,
        limit: pageSize,
        excludeSources,
        signal: controller.signal,
      });
      return {
        items: appendUnique(entries.get(key)?.items ?? items, page.sessions),
        cursor: page.cursor,
        hasMore: page.has_more,
        total: page.total,
      };
    },
    onAuthError,
  );
}

function useDebouncedValue<T>(value: T, delayMs: number): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    if (Object.is(value, debounced)) return;
    const timer = setTimeout(() => setDebounced(value), delayMs);
    return () => clearTimeout(timer);
  }, [value, debounced, delayMs]);
  return debounced;
}

export interface SessionHistoryOptions {
  host: string;
  token: string;
  query: string;
  /** Load history rows; when false only `total` is kept fresh. */
  withItems: boolean;
  /** `history_revision` from the latest session list frame. */
  revision: string;
  excludeSources?: readonly string[];
  pageSize?: number;
  /** When false the hook neither fetches nor reports history. */
  enabled?: boolean;
  onAuthError?: () => void;
}

export interface SessionHistoryView {
  items: SessionRecord[];
  /** False until the first rows for the current query arrive. */
  loaded: boolean;
  total: number | null;
  hasMore: boolean;
  loading: boolean;
  /** True while the query typed is still waiting on its debounce. */
  pending: boolean;
  error: string | null;
  loadMore: () => void;
  retry: () => void;
}

export function useSessionHistory({
  host,
  token,
  query,
  withItems,
  revision,
  excludeSources = NO_SOURCES,
  pageSize = 20,
  enabled = true,
  onAuthError,
}: SessionHistoryOptions): SessionHistoryView {
  const trimmed = query.trim();
  const debouncedQuery = useDebouncedValue(trimmed, QUERY_DEBOUNCE_MS);
  const key = enabled && host && token ? entryKey(host, debouncedQuery, excludeSources, withItems) : null;
  if (key && !entries.has(key)) {
    // Seeding the store during render is idempotent and lets the first render
    // read a stable snapshot instead of a placeholder.
    entries.set(key, {
      host,
      query: debouncedQuery,
      excludeSources,
      withItems,
      items: null,
      cursor: null,
      hasMore: withItems,
      total: null,
      revision: null,
      stale: false,
      loading: false,
      error: null,
    });
  }
  const entry = useSyncExternalStore(
    subscribe,
    () => (key ? entries.get(key) ?? null : null),
    () => null,
  );
  const authRef = useRef(onAuthError);
  useEffect(() => {
    authRef.current = onAuthError;
  }, [onAuthError]);
  const lastRun = useRef<{ key: string; revision: string } | null>(null);
  const stale = entry?.stale ?? false;

  useEffect(() => {
    // An empty revision means no list frame has arrived yet; the first one
    // triggers the fetch, so starting earlier would only repeat it.
    if (!key || !revision) return;
    const previous = lastRun.current;
    lastRun.current = { key, revision };
    const fresh = !previous || previous.key !== key;
    if (!fresh && previous.revision === revision && !stale) return;
    if (!fresh && entries.get(key)?.revision === revision && !stale) return;
    const timer = setTimeout(
      () => void revalidate(key, token, revision, pageSize, () => authRef.current?.()),
      fresh ? 0 : REVISION_DEBOUNCE_MS,
    );
    return () => clearTimeout(timer);
  }, [key, revision, stale, token, pageSize]);

  const more = useCallback(() => {
    if (key) loadMore(key, token, pageSize, () => authRef.current?.());
  }, [key, token, pageSize]);

  const retry = useCallback(() => {
    if (key) void revalidate(key, token, revision, pageSize, () => authRef.current?.());
  }, [key, token, revision, pageSize]);

  // Until the typed query settles, the cached rows belong to the previous
  // query; report none rather than show rows that don't match what's typed.
  const pending = trimmed !== debouncedQuery;
  const current = pending ? null : entry;
  return {
    items: current?.items ?? [],
    loaded: current?.items != null,
    total: current?.total ?? null,
    hasMore: current?.hasMore ?? false,
    loading: entry?.loading ?? false,
    pending,
    error: entry?.error ?? null,
    loadMore: more,
    retry,
  };
}

/**
 * Interleave live sessions with loaded history by recency. While more history
 * remains on the server, live rows older than the oldest loaded history row are
 * held back so the visible prefix never reorders as later pages arrive.
 */
export function mergeByRecency(
  live: SessionRecord[],
  history: SessionRecord[],
  historyExhausted: boolean,
): SessionRecord[] {
  const liveIds = new Set(live.map((session) => session.id));
  const older = history.filter((session) => !liveIds.has(session.id));
  const floor =
    historyExhausted || older.length === 0 ? null : older[older.length - 1].last_event_at;
  const eligible = floor === null ? live : live.filter((session) => session.last_event_at >= floor);
  return [...eligible, ...older].sort(byRecency);
}

export interface MergedSessionPage {
  items: SessionRecord[];
  total: number;
  totalPages: number;
  /** History rows for this page are still on their way. */
  filling: boolean;
}

const NO_SESSIONS: SessionRecord[] = [];

/**
 * One page of live + history sessions, fetching more history as paging needs
 * it. `lead` rows (e.g. pinned matches) precede the merged list.
 */
export function useMergedSessionPage(
  live: SessionRecord[],
  history: SessionHistoryView,
  includeHistory: boolean,
  page: number,
  pageSize: number,
  lead: SessionRecord[] = NO_SESSIONS,
): MergedSessionPage {
  const merged = useMemo(
    () => [
      ...lead,
      ...(includeHistory
        ? mergeByRecency(live, history.items, history.loaded && !history.hasMore)
        : [...live].sort(byRecency)),
    ],
    [lead, live, history.items, history.loaded, history.hasMore, includeHistory],
  );
  const needed = page * pageSize;
  const short = includeHistory && merged.length < needed && history.hasMore;
  const { loadMore, loading, error, loaded } = history;
  useEffect(() => {
    if (short && loaded && !loading && !error) loadMore();
  }, [short, loaded, loading, error, loadMore]);
  const total =
    lead.length + live.length + (includeHistory ? (history.total ?? history.items.length) : 0);
  return {
    items: merged.slice((page - 1) * pageSize, needed),
    total,
    totalPages: Math.max(1, Math.ceil(total / pageSize)),
    filling: short || (includeHistory && !loaded),
  };
}
