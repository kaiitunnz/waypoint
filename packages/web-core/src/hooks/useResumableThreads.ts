"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchBackendThreads, isAuthError } from "../api";
import type { ThreadSummary } from "../launch/threads";
import type { Backend } from "../types";

interface ResumableThreadsOptions {
  host: string;
  token: string;
  // Agents that discover threads on the selected host or target.
  backends: Backend[];
  launchTargetId: string;
  // The launch form's agent and account profile: that agent's list is scoped to
  // the profile's thread store.
  scopeBackend: Backend | null;
  scopeAccountProfileId: string | null;
  // False skips fetching, for a surface that lists threads only when shown.
  enabled?: boolean;
  // Both must be stable.
  onAuthFailure: () => void;
  onError?: (message: string) => void;
}

export interface ResumableThreads {
  threadsByBackend: Record<Backend, ThreadSummary[]>;
  loadingByBackend: Record<Backend, boolean>;
  // Drops a thread once it is imported or deleted.
  removeThread: (backend: Backend, threadId: string) => void;
}

export function useResumableThreads({
  host,
  token,
  backends,
  launchTargetId,
  scopeBackend,
  scopeAccountProfileId,
  enabled = true,
  onAuthFailure,
  onError,
}: ResumableThreadsOptions): ResumableThreads {
  const [threadsByBackend, setThreadsByBackend] = useState<Record<Backend, ThreadSummary[]>>({});
  const [loadingByBackend, setLoadingByBackend] = useState<Record<Backend, boolean>>({});
  // A stable key, so a new array with the same agents does not refetch.
  const backendsKey = JSON.stringify(backends);

  useEffect(() => {
    if (!host || !token || !enabled) {
      setThreadsByBackend({});
      setLoadingByBackend({});
      return;
    }
    let active = true;
    const ids = JSON.parse(backendsKey) as Backend[];
    setLoadingByBackend((current) => Object.fromEntries(ids.map((id) => [id, current[id] ?? true])));
    setThreadsByBackend((current) =>
      // The scoped agent is moving to (or away from) a profile: drop its old list
      // so a slow request cannot show the previous profile's threads.
      Object.fromEntries(ids.map((id) => [id, id === scopeBackend ? [] : (current[id] ?? [])])),
    );
    for (const id of ids) {
      setLoadingByBackend((current) => ({ ...current, [id]: true }));
      const accountProfileId = id === scopeBackend ? scopeAccountProfileId || undefined : undefined;
      fetchBackendThreads<ThreadSummary>(host, token, id, {
        launchTargetId: launchTargetId || undefined,
        accountProfileId,
      })
        .then((threads) => {
          if (active) setThreadsByBackend((current) => ({ ...current, [id]: threads }));
        })
        .catch((fetchError) => {
          if (!active) return;
          if (isAuthError(fetchError)) {
            onAuthFailure();
            return;
          }
          onError?.(fetchError instanceof Error ? fetchError.message : `failed to fetch ${id} threads`);
        })
        .finally(() => {
          if (active) setLoadingByBackend((current) => ({ ...current, [id]: false }));
        });
    }
    return () => {
      active = false;
    };
  }, [host, token, enabled, backendsKey, launchTargetId, scopeBackend, scopeAccountProfileId, onAuthFailure, onError]);

  const removeThread = useCallback((backend: Backend, threadId: string) => {
    setThreadsByBackend((current) => ({
      ...current,
      [backend]: (current[backend] ?? []).filter((thread) => thread.id !== threadId),
    }));
  }, []);

  return { threadsByBackend, loadingByBackend, removeThread };
}
