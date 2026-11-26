"use client";

import { useEffect, useState } from "react";

import { fetchBackendModels, isAuthError } from "../api";
import type { Backend, BackendModelListResponse } from "../types";

// The models an agent offers on this host or target and profile. A response for
// an agent, target or profile no longer selected is dropped. On failure the list
// is empty, leaving only the default. `onAuthFailure` must be stable.
export function useLaunchModels(
  host: string,
  token: string,
  backend: Backend,
  launchTargetId: string | null,
  accountProfileId: string | null,
  onAuthFailure?: () => void,
): BackendModelListResponse | null {
  const [loaded, setLoaded] = useState<{ key: string; response: BackendModelListResponse | null } | null>(null);
  const key = `${backend}:${launchTargetId ?? ""}:${accountProfileId ?? ""}`;

  useEffect(() => {
    if (!host || !token) return;
    let cancelled = false;
    fetchBackendModels(host, token, backend, { launchTargetId, accountProfileId })
      .then((response) => {
        if (!cancelled) setLoaded({ key, response });
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthError(error)) {
          onAuthFailure?.();
          return;
        }
        setLoaded({ key, response: null });
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, backend, launchTargetId, accountProfileId, key, onAuthFailure]);

  return loaded?.key === key ? loaded.response : null;
}
