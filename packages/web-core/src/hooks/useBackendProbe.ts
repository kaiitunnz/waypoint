"use client";

import { useEffect, useState } from "react";

import { probeBackend } from "../api";

// A host that never answers (a dropped route) reads as unreachable after this long.
const PROBE_TIMEOUT_MS = 6000;
const PROBE_DEBOUNCE_MS = 350;

export type ProbeStatus = "idle" | "checking" | "reachable" | "unreachable";

// One reachability probe (GET {url}/health) of the host the user chose. It re-runs
// only when `url` changes, debounced so typing a port does not probe per keystroke;
// a null `url` probes nothing.
export function useBackendProbe(url: string | null): ProbeStatus {
  const [result, setResult] = useState<{ url: string; ok: boolean } | null>(null);

  useEffect(() => {
    if (!url) {
      return;
    }
    let active = true;
    const controller = new AbortController();
    let giveUp: ReturnType<typeof setTimeout> | undefined;
    const timer = setTimeout(() => {
      giveUp = setTimeout(() => controller.abort(), PROBE_TIMEOUT_MS);
      probeBackend(url, controller.signal).then((ok) => {
        clearTimeout(giveUp);
        if (active) setResult({ url, ok });
      });
    }, PROBE_DEBOUNCE_MS);
    return () => {
      active = false;
      clearTimeout(timer);
      clearTimeout(giveUp);
      controller.abort();
    };
  }, [url]);

  if (!url) return "idle";
  if (result?.url === url) return result.ok ? "reachable" : "unreachable";
  return "checking";
}
