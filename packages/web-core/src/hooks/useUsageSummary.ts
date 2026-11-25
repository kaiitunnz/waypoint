"use client";

import { useCallback, useEffect, useState } from "react";

import { fetchUsageDashboard, isAuthError, refreshUsageDashboard } from "../api";
import type { UsageDashboardBucket, UsageDashboardResponse } from "../types";

export interface UsageSummaryState {
  // Null until the first fetch lands, and again when usage is unavailable.
  buckets: UsageDashboardBucket[] | null;
  refreshing: boolean;
  refresh: () => void;
}

// One rate-limit snapshot per account from /api/usage, fetched once; no polling.
// `onAuthFailure` must be stable.
export function useUsageSummary(host: string, token: string, onAuthFailure?: () => void): UsageSummaryState {
  const [response, setResponse] = useState<UsageDashboardResponse | null>(null);
  const [refreshing, setRefreshing] = useState(false);

  useEffect(() => {
    if (!host || !token) return;
    let active = true;
    fetchUsageDashboard(host, token)
      .then((next) => {
        if (active) setResponse(next);
      })
      .catch((error) => {
        if (!active) return;
        setResponse(null);
        if (isAuthError(error)) onAuthFailure?.();
      });
    return () => {
      active = false;
      setResponse(null);
    };
  }, [host, token, onAuthFailure]);

  const refresh = useCallback(() => {
    if (!host || !token) return;
    setRefreshing(true);
    refreshUsageDashboard(host, token)
      .then(setResponse)
      .catch((error) => {
        if (isAuthError(error)) onAuthFailure?.();
      })
      .finally(() => setRefreshing(false));
  }, [host, token, onAuthFailure]);

  return {
    buckets: response?.buckets ?? null,
    refreshing,
    refresh,
  };
}
