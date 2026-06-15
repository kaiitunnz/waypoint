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

const FALLBACK_TRANSPORT_LABELS: Record<string, string> = {
  codex_app_server: "codex app server",
  claude_cli: "claude cli",
  claude_tty: "claude tty",
  opencode_http: "opencode http",
  tmux: "tmux",
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

/**
 * Backend-agnostic helpers that consult the catalog when present and
 * fall back to the hand-mirrored defaults for the built-ins so
 * pre-bootstrap callers (login screen, error boundaries) still render
 * sensibly without a hook.
 */
export function transportLabel(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): string {
  return (
    catalog?.transportLabel(transport)?.toLowerCase() ??
    FALLBACK_TRANSPORT_LABELS[transport] ??
    transport
  );
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

// Friendly, user-facing names for the launch transport picker. Distinct from
// `transportLabel()`, which surfaces the raw descriptor label (e.g. "claude
// cli") for badges and status lines. Falls back to a capability-derived label
// so a newly-registered transport still renders sensibly without a frontend
// edit.
const TRANSPORT_PICKER_LABELS: Record<string, string> = {
  claude_cli: "Structured",
  claude_tty: "Terminal UI",
  codex_app_server: "Structured",
  opencode_http: "Structured",
  tmux: "Terminal (raw)",
};

export function transportPickerLabel(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): string {
  return (
    TRANSPORT_PICKER_LABELS[transport] ??
    (liveTerminal(transport, catalog) ? "Terminal" : "Structured")
  );
}

const TRANSPORT_FIDELITY_HINTS: Record<string, string> = {
  claude_cli: "Native structured adapter — full-fidelity transcript cards.",
  claude_tty: "Real Claude Code terminal UI, tailed live — resumable.",
  codex_app_server: "Native structured adapter — full-fidelity transcript cards.",
  opencode_http: "Native structured adapter — full-fidelity transcript cards.",
  tmux: "Generic terminal pane — live output, heuristic transcript.",
};

// A transport's fidelity for the launch picker: a coarse `kind` tag (drives the
// visual indicator) plus a one-line trade-off hint. Both derive from the live
// transport capabilities, with a friendlier per-transport hint when known.
export function transportFidelity(
  transport: SessionTransport,
  catalog?: BackendCatalog,
): { kind: "structured" | "terminal"; hint: string } {
  const structured =
    fidelityFor(transport, catalog) === "structured" &&
    !liveTerminal(transport, catalog);
  const kind = structured ? "structured" : "terminal";
  const hint =
    TRANSPORT_FIDELITY_HINTS[transport] ??
    (kind === "structured"
      ? "Structured transcript — full-fidelity cards."
      : "Live terminal — heuristic transcript.");
  return { kind, hint };
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
