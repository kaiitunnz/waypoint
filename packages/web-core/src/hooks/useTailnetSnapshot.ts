"use client";

import { useCallback, useEffect, useState } from "react";

import {
  fetchBackendTailnetSnapshot,
  fetchLocalTailnetSnapshot,
  type TailnetSnapshot,
} from "../tailnet";

// `local`: the web app's own `/api/tailnet/peers` route (before sign-in).
// `backend`: the signed-in backend's route, for the host switcher.
export type TailnetSource = { kind: "local" } | { kind: "backend"; host: string; token: string };

export interface TailnetSnapshotState {
  snapshot: TailnetSnapshot | null;
  loading: boolean;
  refresh: () => void;
}

const FAILED: TailnetSnapshot = { available: false, error: "discovery failed", peers: [] };

function sourceKey(source: TailnetSource | null): string {
  if (!source) return "";
  return source.kind === "local" ? "local" : `backend\n${source.host}\n${source.token}`;
}

// Tailscale peers for the host picker, loaded when `source` is set and again on
// `refresh`. The previous snapshot stays visible while a refresh runs.
export function useTailnetSnapshot(source: TailnetSource | null): TailnetSnapshotState {
  const [reload, setReload] = useState(0);
  const [result, setResult] = useState<{
    sourceKey: string;
    request: string;
    snapshot: TailnetSnapshot;
  } | null>(null);
  const key = sourceKey(source);
  const request = `${key}#${reload}`;

  useEffect(() => {
    if (!key) {
      return;
    }
    const controller = new AbortController();
    const load =
      key === "local"
        ? fetchLocalTailnetSnapshot(controller.signal)
        : (() => {
            const [, host, token] = key.split("\n");
            return fetchBackendTailnetSnapshot(host, token, controller.signal);
          })();
    load
      .catch(() => FAILED)
      .then((snapshot) => {
        if (!controller.signal.aborted) setResult({ sourceKey: key, request, snapshot });
      });
    return () => controller.abort();
  }, [key, request]);

  const refresh = useCallback(() => setReload((count) => count + 1), []);
  const snapshot = key && result?.sourceKey === key ? result.snapshot : null;
  return { snapshot, loading: !!key && result?.request !== request, refresh };
}
