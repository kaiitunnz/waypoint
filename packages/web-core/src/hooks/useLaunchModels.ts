"use client";

import { useEffect, useState } from "react";

import { fetchBackendModels, isAuthError } from "../api";
import type { Backend, BackendModelListResponse } from "../types";

// The models an agent offers on this host or target and profile, or null while
// loading, disabled or failed. A response for an agent, target or profile no
// longer selected is dropped. `onAuthFailure` must be stable.
export function useLaunchModels(
  host: string,
  token: string,
  backend: Backend,
  launchTargetId: string | null,
  accountProfileId: string | null,
  onAuthFailure: () => void,
  enabled = true,
): BackendModelListResponse | null {
  const [loaded, setLoaded] = useState<{ key: string; response: BackendModelListResponse | null } | null>(null);
  const key = `${backend}:${launchTargetId ?? ""}:${accountProfileId ?? ""}`;

  useEffect(() => {
    if (!host || !token || !enabled) return;
    let cancelled = false;
    fetchBackendModels(host, token, backend, { launchTargetId, accountProfileId })
      .then((response) => {
        if (!cancelled) setLoaded({ key, response });
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthError(error)) {
          onAuthFailure();
          return;
        }
        setLoaded({ key, response: null });
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, backend, launchTargetId, accountProfileId, key, onAuthFailure, enabled]);

  return enabled && loaded?.key === key ? loaded.response : null;
}
