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
  BackendPermissionMode,
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

const FALLBACK_TRANSPORT_LABELS: Record<string, string> = {
  codex_app_server: "codex app server",
  claude_cli: "claude cli",
  tmux: "tmux",
};

// Hand-mirrored from the backend's BackendCapabilities defaults so
// catalog-less callers (early bootstrap, lib helpers) still get the
// right answer for the two built-ins. New backends MUST be reachable
// via the live catalog — these fallbacks only cover today's known
// transports.
const FALLBACK_STRUCTURED = new Set(["codex_app_server", "claude_cli"]);
const FALLBACK_RESUMABLE = new Set(["tmux"]);

const FALLBACK_PERMISSION_MODES: Record<string, BackendPermissionMode[]> = {
  claude_code: [
    { id: "default", label: "Default" },
    { id: "plan", label: "Plan" },
    { id: "acceptEdits", label: "Accept Edits" },
    { id: "auto", label: "Auto" },
    { id: "bypassPermissions", label: "Bypass Permissions" },
    { id: "dontAsk", label: "Don't Ask" },
  ],
  codex: [
    { id: "default", label: "Default" },
    { id: "auto_review", label: "Auto-review" },
    { id: "full_access", label: "Full Access" },
  ],
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

/**
 * Backend-agnostic helpers that consult the catalog when present and
 * fall back to the hand-mirrored defaults for the two built-ins so
 * pre-bootstrap callers (login screen, error boundaries) still render
 * sensibly without a hook.
 */
export function transportLabel(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): string {
  return (
    catalog?.byTransport(transport)?.label.toLowerCase() ??
    FALLBACK_TRANSPORT_LABELS[transport] ??
    transport
  );
}

export function fidelityFor(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): "structured" | "heuristic" {
  const caps = catalog?.byTransport(transport)?.capabilities;
  const structured = caps
    ? caps.is_structured
    : FALLBACK_STRUCTURED.has(transport);
  return structured ? "structured" : "heuristic";
}

export function supportsResume(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.byTransport(transport)?.capabilities;
  return caps ? caps.supports_resume : FALLBACK_RESUMABLE.has(transport);
}

export function supportsStructuredApproval(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.byTransport(transport)?.capabilities;
  return caps ? caps.is_structured : FALLBACK_STRUCTURED.has(transport);
}

export function permissionModesFor(
  backend: Backend,
  catalog?: BackendCatalog,
): readonly BackendPermissionMode[] {
  const live = catalog?.byId(backend)?.capabilities.permission_modes;
  if (live && live.length > 0) return live;
  return FALLBACK_PERMISSION_MODES[backend] ?? [];
}

export function permissionModeLabel(
  backend: Backend,
  value: string | null | undefined,
  catalog?: BackendCatalog,
): string | null {
  if (!value) return null;
  const match = permissionModesFor(backend, catalog).find(
    (mode) => mode.id === value,
  );
  return match?.label ?? value;
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
