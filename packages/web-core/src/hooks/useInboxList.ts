"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { connectSessionsSocket, fetchInboxList, isAuthError } from "../api";
import {
  appendInboxPage,
  applyInboxUpdate,
  fetchInboxUpTo,
  INBOX_PAGE_LIMIT,
  parseInboxUpdate,
  type InboxStatusFilter,
  type InboxSubscribe,
  type InboxUpdate,
} from "../inbox";
import type { InboxItem, SessionEnvelope } from "../types";

const SEARCH_DEBOUNCE_MS = 250;

interface InboxListOptions {
  host: string;
  token: string;
  status: InboxStatusFilter;
  // The search as typed; requests follow it once it settles.
  q: string;
  // Must be stable: a new one restarts the requests and the socket.
  onAuthFailure: () => void;
  // Without it the list opens its own socket. Must be stable.
  subscribe?: InboxSubscribe;
  enabled?: boolean;
}

export interface InboxList {
  items: InboxItem[];
  // The search being requested: `q` once it settles.
  query: string;
  // The items are those of the current status and search.
  loaded: boolean;
  loading: boolean;
  hasMore: boolean;
  // Why the filter's first page could not be read.
  error: string | null;
  loadMore: () => void;
  retry: () => void;
  // Drops items ahead of the live update that removes them.
  remove: (ids: ReadonlySet<string>) => void;
}

const filterKey = (status: InboxStatusFilter, q: string) => `${status}|${q}`;

export function useInboxList({
  host,
  token,
  status,
  q,
  onAuthFailure,
  subscribe,
  enabled = true,
}: InboxListOptions): InboxList {
  const [items, setItems] = useState<InboxItem[]>([]);
  const [cursor, setCursor] = useState<string | null>(null);
  const [hasMore, setHasMore] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loadedFor, setLoadedFor] = useState("");
  const [query, setQuery] = useState(q);
  const [attempt, setAttempt] = useState(0);
  const filterRef = useRef({ status, q: query });
  const itemsRef = useRef(items);
  // Responses to a request made for an earlier filter are dropped.
  const generation = useRef(0);

  useEffect(() => {
    const timer = setTimeout(() => setQuery(q), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [q]);

  useEffect(() => {
    filterRef.current = { status, q: query };
  }, [status, query]);

  useEffect(() => {
    itemsRef.current = items;
  }, [items]);

  const params = useCallback(
    (page: { cursor?: string | null; limit: number }) => ({
      status: status === "all" ? undefined : status,
      q: query || undefined,
      ...page,
    }),
    [status, query],
  );

  useEffect(() => {
    if (!host || !token || !enabled) return;
    let active = true;
    generation.current += 1;
    setLoading(true);
    fetchInboxList(host, token, params({ limit: INBOX_PAGE_LIMIT }))
      .then((page) => {
        if (!active) return;
        setItems(page.items);
        setCursor(page.cursor);
        setHasMore(page.hasMore);
        setError(null);
        setLoadedFor(filterKey(status, query));
      })
      .catch((reason) => {
        if (!active) return;
        if (isAuthError(reason)) onAuthFailure();
        else setError(reason instanceof Error ? reason.message : "failed to fetch inbox");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [host, token, status, query, enabled, attempt, params, onAuthFailure]);

  // After a reconnect, re-reads what is shown in one update.
  const refresh = useCallback(() => {
    const started = generation.current;
    fetchInboxUpTo(
      (from, limit) => fetchInboxList(host, token, params({ cursor: from, limit })),
      itemsRef.current.length,
    )
      .then((page) => {
        if (started !== generation.current) return;
        setItems(page.items);
        setCursor(page.cursor);
        setHasMore(page.hasMore);
        setError(null);
        // This may be the first read of the filter to succeed.
        setLoadedFor(filterKey(status, query));
      })
      .catch((reason) => {
        if (isAuthError(reason)) onAuthFailure();
      });
  }, [host, token, status, query, params, onAuthFailure]);
  const refreshRef = useRef(refresh);
  useEffect(() => {
    refreshRef.current = refresh;
  }, [refresh]);

  const apply = useCallback((update: InboxUpdate) => {
    setItems((prev) => applyInboxUpdate(prev, update, filterRef.current));
  }, []);

  useEffect(() => {
    if (!host || !token || !enabled) return;
    if (subscribe) {
      return subscribe((event) => {
        if (event === "reconnected") refreshRef.current();
        else apply(event);
      });
    }
    let active = true;
    let socket: WebSocket | null = null;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;
    let retries = 0;

    function connect() {
      socket = connectSessionsSocket(
        host,
        token,
        (message: SessionEnvelope) => {
          if (message.type !== "inbox_update") return;
          apply(parseInboxUpdate(message.payload));
        },
        () => {
          if (active) onAuthFailure();
        },
        {
          onOpen: () => {
            retries = 0;
          },
          onClose: () => {
            if (!active) return;
            const delay = Math.min(15000, 500 * 2 ** retries);
            retries += 1;
            reconnectTimer = setTimeout(connect, delay);
          },
        },
      );
    }
    connect();

    return () => {
      active = false;
      if (reconnectTimer !== null) clearTimeout(reconnectTimer);
      socket?.close();
    };
  }, [host, token, enabled, subscribe, apply, onAuthFailure]);

  const loadMore = useCallback(() => {
    if (!cursor || loading || !host || !token) return;
    const started = generation.current;
    setLoading(true);
    fetchInboxList(host, token, params({ cursor, limit: INBOX_PAGE_LIMIT }))
      .then((page) => {
        if (started !== generation.current) return;
        setItems((prev) => appendInboxPage(prev, page.items));
        setCursor(page.cursor);
        setHasMore(page.hasMore);
      })
      .catch((reason) => {
        if (isAuthError(reason)) onAuthFailure();
      })
      .finally(() => {
        if (started === generation.current) setLoading(false);
      });
  }, [cursor, loading, host, token, params, onAuthFailure]);

  const retry = useCallback(() => setAttempt((value) => value + 1), []);
  const remove = useCallback((ids: ReadonlySet<string>) => {
    setItems((prev) => prev.filter((item) => !ids.has(item.id)));
  }, []);

  const loaded = loadedFor === filterKey(status, query);
  return { items, query, loaded, loading, hasMore, error, loadMore, retry, remove };
}
