"use client";

/**
 * Backend-catalog helpers.
 *
 * The backend ships a `/api/backends` endpoint listing every registered
 * plugin's id, label, badges, and capability descriptor. The frontend
 * consults this catalog instead of mirroring backend constants in
 * TypeScript so adding a new plugin doesn't require a frontend edit.
 *
 * A session is an (agent, transport) pair, so its effective capabilities are
 * *composed*: agent-level fields (permission modes, slash commands, fork /
 * thread support) come from the agent plugin, while transport-level fields
 * (structured vs heuristic, resume, live terminal) come from the transport.
 * `capsFor(backend, transport)` resolves that pair; the per-axis helpers below
 * read whichever half they belong to.
 *
 * The hook reads from `me.backends` first (already loaded during the
 * auth bootstrap) and falls back to the dedicated endpoint when the
 * caller starts without a `MeResponse`.
 */

import { useEffect, useMemo, useState } from "react";

import { fetchBackends } from "@/lib/api";
import type {
  AgentCapabilities,
  Backend,
  BackendCapabilities,
  BackendDescriptor,
  BackendPermissionMode,
  LaunchMode,
  MeResponse,
  SessionTransport,
  TransportCapabilities,
} from "@/lib/types";

export interface BackendCatalog {
  byId: (id: Backend) => BackendDescriptor | undefined;
  // Compose a session's capabilities from its (agent, transport) pair. Returns
  // undefined only when the agent is unknown to the live catalog.
  capsFor: (
    backend: Backend,
    transport: SessionTransport,
  ) => BackendCapabilities | undefined;
  agentCaps: (backend: Backend) => AgentCapabilities | undefined;
  transportCaps: (transport: SessionTransport) => TransportCapabilities | undefined;
  // Human label for a transport id, sourced from the descriptor that owns it.
  transportLabel: (transport: SessionTransport) => string | undefined;
  all: () => BackendDescriptor[];
  ids: () => Backend[];
  // Pulled from `byId(id)?.label`, with a humane fallback so a backend
  // that disappears mid-session still renders something sensible.
  labelFor: (id: Backend) => string;
}

const FALLBACK_LABELS: Record<string, string> = {
  claude_code: "Claude Code",
  claude_tty: "Claude TUI",
  codex: "Codex",
  opencode: "OpenCode",
  tmux: "Tmux",
};

// Hand-mirrored from the backend's capability defaults so catalog-less callers
// (early bootstrap, lib helpers) still get the right answer for the built-ins.
// New backends MUST be reachable via the live catalog — these fallbacks only
// cover today's known agents/transports. Transport-keyed sets resolve
// transport-level flags; backend-keyed sets resolve agent-level ones.
const FALLBACK_STRUCTURED = new Set([
  "codex_app_server",
  "claude_cli",
  "claude_tty",
  "opencode_http",
]);
const FALLBACK_RESUMABLE = new Set(["tmux", "claude_tty"]);
const FALLBACK_LIVE_TERMINAL = new Set(["tmux"]);
const FALLBACK_APPROVAL_NOTE = new Set(["claude_code", "opencode"]);
const FALLBACK_PLAN_APPROVAL = new Set(["codex"]);
// Every agent can fork except the generic tmux fallback.
const FALLBACK_NO_FORK = new Set(["tmux"]);

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
  // Transport-level data is keyed by transport id so a session whose transport
  // differs from its agent's native one (e.g. a tmux-wrapped Claude) resolves
  // the transport's caps, not the agent's defaults.
  const transportCapsMap = new Map<string, TransportCapabilities>();
  const transportLabelMap = new Map<string, string>();
  for (const item of descriptors) {
    byIdMap.set(item.id, item);
    transportCapsMap.set(item.transport_id, item.transport_capabilities);
    transportLabelMap.set(item.transport_id, item.label);
  }
  const agentCaps = (backend: Backend) => byIdMap.get(backend)?.agent_capabilities;
  const transportCaps = (transport: SessionTransport) =>
    transportCapsMap.get(transport);
  return {
    byId: (id) => byIdMap.get(id),
    agentCaps,
    transportCaps,
    capsFor: (backend, transport) => {
      const agent = agentCaps(backend);
      if (!agent) return undefined;
      // Fall back to the agent's own transport caps when the requested
      // transport is unknown, so a stale transport id still yields a usable
      // descriptor rather than throwing.
      const tport =
        transportCaps(transport) ?? byIdMap.get(backend)?.transport_capabilities;
      if (!tport) return undefined;
      return { ...agent, ...tport };
    },
    transportLabel: (transport) => transportLabelMap.get(transport),
    all: () => [...descriptors],
    ids: () => descriptors.map((d) => d.id),
    labelFor: (id) => byIdMap.get(id)?.label ?? FALLBACK_LABELS[id] ?? id,
  };
}

export function humaniseBackend(id: Backend, catalog?: BackendCatalog): string {
  return catalog?.byId(id)?.label ?? FALLBACK_LABELS[id] ?? id;
}

// How a transport is presented in the UI: a user-facing name, a one-line
// description, and a coarse kind that drives the picker icon. This map is the
// single source of truth — picker labels, session-card chips, and transport
// badges all read from it. Unknown transports fall back to a capability-derived
// presentation so a newly-registered transport still renders sensibly.
export interface TransportPresentation {
  name: string;
  description: string;
  kind: "chat" | "terminal";
}

const TRANSPORT_PRESENTATION: Record<string, TransportPresentation> = {
  claude_cli: {
    name: "Chat",
    description: "Native chat interface backed by a structured event stream.",
    kind: "chat",
  },
  claude_tty: {
    name: "Emulated",
    description: "Emulated chat interface wrapping the TUI app.",
    kind: "chat",
  },
  codex_app_server: {
    name: "Chat",
    description: "Native chat interface backed by a structured event stream.",
    kind: "chat",
  },
  opencode_http: {
    name: "Chat",
    description: "Native chat interface backed by a structured event stream.",
    kind: "chat",
  },
  tmux: {
    name: "Terminal",
    description: "Pass-through TUI for the raw terminal experience.",
    kind: "terminal",
  },
};

export function transportPresentation(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): TransportPresentation {
  const known = TRANSPORT_PRESENTATION[transport];
  if (known) return known;
  if (liveTerminal(transport, catalog)) {
    return {
      name: "Terminal",
      description: "Pass-through TUI for the raw terminal experience.",
      kind: "terminal",
    };
  }
  return {
    name: "Chat",
    description: "Native chat interface backed by a structured event stream.",
    kind: "chat",
  };
}

// The user-facing transport name, sourced from the presentation map.
export function transportLabel(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): string {
  return transportPresentation(transport, catalog).name;
}

export function fidelityFor(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): "structured" | "heuristic" {
  const caps = catalog?.transportCaps(transport);
  const structured = caps
    ? caps.is_structured
    : FALLBACK_STRUCTURED.has(transport);
  return structured ? "structured" : "heuristic";
}

export function supportsResume(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.transportCaps(transport);
  return caps ? caps.supports_resume : FALLBACK_RESUMABLE.has(transport);
}

// Whether the transport renders a live terminal (xterm pane + WS stream)
// rather than a structured chat transcript.
export function liveTerminal(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.transportCaps(transport);
  return caps ? caps.live_terminal : FALLBACK_LIVE_TERMINAL.has(transport);
}

// Reattach-after-exit is a transport-level property, advertised on each
// agent's descriptor for its native transport. Keyed by agent id so callers
// holding a `SessionRecord.backend` resolve it directly.
export function supportsReattachAfterExit(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.byId(backend)?.transport_capabilities;
  return caps ? Boolean(caps.supports_reattach_after_exit) : false;
}

export function supportsStructuredApproval(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.transportCaps(transport);
  return caps ? caps.is_structured : FALLBACK_STRUCTURED.has(transport);
}

export function supportsApprovalNote(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.agentCaps(backend);
  return caps ? caps.supports_approval_note : FALLBACK_APPROVAL_NOTE.has(backend);
}

export function supportsAttachments(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  return Boolean(catalog?.agentCaps(backend)?.supports_attachments);
}

// Whether the agent can fork the current thread into a new session.
export function supportsFork(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.agentCaps(backend);
  return caps ? caps.supports_fork : !FALLBACK_NO_FORK.has(backend);
}

// Which approval decisions a backend honours (e.g. codex/opencode add
// `acceptForSession`). Drives which escalation buttons the approval card
// renders so it can't offer a decision the backend would map to a plain
// deny. Falls back to the universal approve/decline pair.
export function approvalDecisionsFor(
  backend: Backend,
  catalog?: BackendCatalog,
): readonly string[] {
  const decisions = catalog?.agentCaps(backend)?.approval_decisions;
  return decisions && decisions.length > 0 ? decisions : ["approve", "decline"];
}

export function isManagedLaunchWrapper(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.byId(backend)?.transport_capabilities;
  return caps
    ? Boolean(caps.is_fallback_for_managed_launch)
    : backend === "tmux";
}

export function supportsPlanApproval(
  backend: Backend,
  catalog?: BackendCatalog,
): boolean {
  const caps = catalog?.agentCaps(backend);
  return caps ? Boolean(caps.supports_plan_approval) : FALLBACK_PLAN_APPROVAL.has(backend);
}

export function permissionModesFor(
  backend: Backend,
  catalog?: BackendCatalog,
): readonly BackendPermissionMode[] {
  const live = catalog?.agentCaps(backend)?.permission_modes;
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

// Launch modes (transport choices) available for an agent. "direct" maps to the
// agent's native structured adapter, "tmux_wrapper" to the generic tmux pane,
// and "auto" lets the backend pick. Only offer "direct" when the agent has a
// structured native transport.
export function launchModesFor(
  backend: Backend,
  catalog?: BackendCatalog,
): LaunchMode[] {
  const modes: LaunchMode[] = ["auto"];
  const native = catalog?.byId(backend)?.transport_capabilities;
  const hasStructuredNative = native
    ? native.is_structured
    : !FALLBACK_NO_FORK.has(backend);
  if (hasStructuredNative) modes.push("direct");
  modes.push("tmux_wrapper");
  return modes;
}

// Transport ids that some agent folds in as a NON-native transport — listed in
// a descriptor's `supported_transports` but not its own `transport_id`. An agent
// whose native transport is folded into another agent is therefore not a
// top-level launch entry: e.g. `claude_tty` folds into `claude_code`, and `tmux`
// folds into every agent.
function foldedTransportIds(catalog?: BackendCatalog): Set<string> {
  const folded = new Set<string>();
  for (const descriptor of catalog?.all() ?? []) {
    for (const transport of descriptor.supported_transports) {
      if (transport !== descriptor.transport_id) folded.add(transport);
    }
  }
  return folded;
}

// The agent-primary launch list: registered backends minus the managed-launch
// fallback and minus any agent whose native transport is folded into another
// agent's transport menu. Fully data-driven, so a new agent or a newly-folded
// transport needs no frontend edit.
export function launchableAgents(
  backends: Backend[],
  catalog?: BackendCatalog,
): Backend[] {
  const folded = foldedTransportIds(catalog);
  return backends.filter((id) => {
    if (isManagedLaunchWrapper(id, catalog)) return false;
    const descriptor = catalog?.byId(id);
    if (!descriptor) return true;
    return !folded.has(descriptor.transport_id);
  });
}

// The transports an agent can be launched over, in descriptor order.
export function agentTransports(
  backend: Backend,
  catalog?: BackendCatalog,
): SessionTransport[] {
  return catalog?.byId(backend)?.supported_transports ?? [];
}

// The transport the launch picker should preselect for an agent.
export function defaultTransportFor(
  backend: Backend,
  catalog?: BackendCatalog,
): SessionTransport | null {
  return catalog?.byId(backend)?.default_transport ?? null;
}

// The launchable agent that drives a transport: the non-folded agent whose
// `supported_transports` includes it. Unique for the structured transports
// (claude_cli/claude_tty → claude_code, codex_app_server → codex,
// opencode_http → opencode); for the shared tmux pane it returns the first
// match, so callers that already know the session's own agent should prefer it.
export function agentForTransport(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): Backend | undefined {
  if (!catalog) return undefined;
  const folded = foldedTransportIds(catalog);
  return catalog
    .all()
    .find(
      (descriptor) =>
        !folded.has(descriptor.transport_id) &&
        descriptor.supported_transports.includes(transport),
    )?.id;
}

// Normalise a session's recorded backend to the agent the UI should show.
// Legacy rows stored a folded transport as the backend (e.g. backend=claude_tty),
// so a tty-tail Claude session and a new claude_code+claude_tty launch render
// identically. When the recorded backend is already a launchable agent, trust
// it; only fold transport-as-backend rows back to their owning agent.
export function displayAgentFor(
  backend: Backend,
  transport: SessionTransport,
  catalog?: BackendCatalog,
): Backend {
  if (!catalog) return backend;
  const descriptor = catalog.byId(backend);
  const folded = foldedTransportIds(catalog);
  if (descriptor && !folded.has(descriptor.transport_id)) return backend;
  return agentForTransport(transport, catalog) ?? backend;
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
