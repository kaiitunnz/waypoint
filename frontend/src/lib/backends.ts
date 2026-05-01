"use client";

/**
 * Backend-catalog helpers.
 *
 * The backend ships a `/api/backends` endpoint listing every registered
 * plugin's id, label, badges, and capability descriptor. The frontend
 * consults this catalog instead of mirroring backend constants in
 * TypeScript so adding a new plugin doesn't require a frontend edit.
 *
 * The hook reads from `me.backends` first (already loaded during the
 * auth bootstrap) and falls back to the dedicated endpoint when the
 * caller starts without a `MeResponse`.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchBackends } from "@/lib/api";
import type {
  Backend,
  BackendDescriptor,
  MeResponse,
  SessionTransport,
} from "@/lib/types";

export interface BackendCatalog {
  byId: (id: Backend) => BackendDescriptor | undefined;
  byTransport: (transport: SessionTransport) => BackendDescriptor | undefined;
  all: () => BackendDescriptor[];
  ids: () => Backend[];
  // Pulled from `byId(id)?.label`, with a humane fallback so a backend
  // that disappears mid-session still renders something sensible.
  labelFor: (id: Backend) => string;
}

const FALLBACK_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  codex: "Codex",
  tmux: "Tmux",
};

export function buildCatalog(descriptors: BackendDescriptor[]): BackendCatalog {
  const byIdMap = new Map<string, BackendDescriptor>();
  const byTransportMap = new Map<string, BackendDescriptor>();
  for (const item of descriptors) {
    byIdMap.set(item.id, item);
    byTransportMap.set(item.transport_id, item);
  }
  return {
    byId: (id) => byIdMap.get(id),
    byTransport: (transport) => byTransportMap.get(transport),
    all: () => [...descriptors],
    ids: () => descriptors.map((d) => d.id),
    labelFor: (id) =>
      byIdMap.get(id)?.label ?? FALLBACK_LABELS[id] ?? id,
  };
}

export function humaniseBackend(id: Backend): string {
  return FALLBACK_LABELS[id] ?? id;
}

/** Single-flight catalog hook fed by `MeResponse.backends`. */
export function useBackendCatalog(
  host: string | null,
  token: string | null,
  me: MeResponse | null,
): BackendCatalog {
  const [descriptors, setDescriptors] = useState<BackendDescriptor[]>(
    () => me?.backends ?? [],
  );

  useEffect(() => {
    if (me?.backends && me.backends.length > 0) {
      setDescriptors(me.backends);
      return;
    }
    if (!host || !token) return;
    let cancelled = false;
    fetchBackends(host, token)
      .then((items) => {
        if (!cancelled) setDescriptors(items);
      })
      .catch(() => {
        // The hook degrades gracefully — `byId` returns `undefined`
        // and components fall back to `humaniseBackend`. Surfacing a
        // toast here would be too noisy when the API is briefly down.
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, me?.backends]);

  return useMemo(() => buildCatalog(descriptors), [descriptors]);
}
