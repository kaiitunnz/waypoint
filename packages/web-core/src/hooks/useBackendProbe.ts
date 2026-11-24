"use client";

import { useEffect, useState } from "react";

import { probeBackend } from "../api";

export type ProbeStatus = "idle" | "checking" | "reachable" | "unreachable";

// One reachability probe (GET {url}/health) of the host the user chose. It
// re-runs only when `url` changes, after `debounceMs` so typing a port does not
// fire a probe per keystroke, and drops a superseded probe's answer. A null
// `url` probes nothing.
export function useBackendProbe(url: string | null, debounceMs = 350): ProbeStatus {
  const [result, setResult] = useState<{ url: string; ok: boolean } | null>(null);

  useEffect(() => {
    if (!url) {
      return;
    }
    const controller = new AbortController();
    const timer = setTimeout(() => {
      probeBackend(url, controller.signal).then((ok) => {
        if (!controller.signal.aborted) setResult({ url, ok });
      });
    }, debounceMs);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [url, debounceMs]);

  if (!url) return "idle";
  if (result?.url === url) return result.ok ? "reachable" : "unreachable";
  return "checking";
}
