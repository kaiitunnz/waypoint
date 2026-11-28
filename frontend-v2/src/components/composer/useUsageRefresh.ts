"use client";

import { useState } from "react";

import { refreshSessionRateLimitUsage } from "@waypoint/web-core/api";
import type { SessionRecord } from "@waypoint/web-core/types";

import { useShellData } from "@/lib/shell-data";
import { useFailureToast } from "@/lib/useFailureToast";

// Re-reads the session's rate limits from the provider and hands the updated record on.
export function useUsageRefresh(session: SessionRecord | null, onSession: (session: SessionRecord) => void) {
  const { host, token } = useShellData();
  const reportFailure = useFailureToast();
  const [refreshing, setRefreshing] = useState(false);

  async function refresh() {
    if (!session || refreshing) return;
    setRefreshing(true);
    try {
      onSession(await refreshSessionRateLimitUsage(host, token, session.id));
    } catch (error) {
      reportFailure(error, "failed to refresh rate-limit usage");
    } finally {
      setRefreshing(false);
    }
  }
  return { refresh, refreshing };
}
