/**
 * Versioned event-envelope parsing.
 *
 * The backend stamps every persisted event with `metadata.version = 1`
 * (see `waypoint.backends.events.EventEnvelope`). This module is the
 * single place the frontend reads the versioned fields out of
 * `metadata`; transcript components (TranscriptCard, ApprovalCard,
 * etc.) consume the typed envelope so adding a v2 field doesn't
 * scatter optional-chain guards across the UI.
 *
 * Unknown versions degrade gracefully: the metadata is still
 * surfaced via `extra` so renderers can fall back to their existing
 * heuristics rather than dropping the event.
 */

import type { EventRecord } from "@/lib/types";

export interface EventEnvelopeItem {
  itemId?: string | null;
  itemType?: string | null;
  toolName?: string | null;
  toolInput?: Record<string, unknown> | null;
  toolUseId?: string | null;
  payload?: Record<string, unknown> | null;
}

export interface EventEnvelopeApproval {
  approvalId?: string | null;
  toolName?: string | null;
  toolInput?: Record<string, unknown> | null;
  decisions?: string[];
}

export interface EventDiffPreviewFile {
  path: string;
  oldPath?: string | null;
  changeType: "add" | "delete" | "update" | "move" | "unknown";
  diff: string;
  additions: number;
  deletions: number;
  truncated: boolean;
  binary: boolean;
  unavailableReason?: string | null;
}

export interface EventDiffPreview {
  schemaVersion: 1;
  phase: "proposed" | "applied" | "aggregate";
  files: EventDiffPreviewFile[];
  totalAdditions: number;
  totalDeletions: number;
  truncated: boolean;
}

export interface EventEnvelope {
  version: number | null;
  kind: EventRecord["kind"];
  text: string;
  status?: string | null;
  item?: EventEnvelopeItem;
  approval?: EventEnvelopeApproval;
  diffPreview?: EventDiffPreview | null;
  // Untyped passthrough for fields not yet promoted to the schema.
  extra: Record<string, unknown>;
}

function readString(metadata: Record<string, unknown>, key: string): string | null {
  const value = metadata[key];
  return typeof value === "string" ? value : null;
}

function readRecord(
  metadata: Record<string, unknown>,
  key: string,
): Record<string, unknown> | null {
  const value = metadata[key];
  if (value && typeof value === "object" && !Array.isArray(value)) {
    return value as Record<string, unknown>;
  }
  return null;
}

function readStringArray(
  metadata: Record<string, unknown>,
  key: string,
): string[] | undefined {
  const value = metadata[key];
  if (Array.isArray(value)) {
    return value.filter((entry): entry is string => typeof entry === "string");
  }
  return undefined;
}

function readNumber(metadata: Record<string, unknown>, key: string): number {
  const value = metadata[key];
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

function readBoolean(metadata: Record<string, unknown>, key: string): boolean {
  return metadata[key] === true;
}

function readDiffPreview(metadata: Record<string, unknown>): EventDiffPreview | null {
  const raw = readRecord(metadata, "diff_preview");
  if (!raw) return null;
  const filesRaw = raw.files;
  if (!Array.isArray(filesRaw)) return null;
  const files = filesRaw
    .filter((entry): entry is Record<string, unknown> =>
      Boolean(entry) && typeof entry === "object" && !Array.isArray(entry),
    )
    .map((entry) => ({
      path: readString(entry, "path") ?? "changes",
      oldPath: readString(entry, "old_path"),
      changeType: readChangeType(entry.change_type),
      diff: readString(entry, "diff") ?? "",
      additions: readNumber(entry, "additions"),
      deletions: readNumber(entry, "deletions"),
      truncated: readBoolean(entry, "truncated"),
      binary: readBoolean(entry, "binary"),
      unavailableReason: readString(entry, "unavailable_reason"),
    }));
  if (!files.length) return null;
  return {
    schemaVersion: 1,
    phase: readDiffPhase(raw.phase),
    files,
    totalAdditions: readNumber(raw, "total_additions"),
    totalDeletions: readNumber(raw, "total_deletions"),
    truncated: readBoolean(raw, "truncated"),
  };
}

function readDiffPhase(value: unknown): EventDiffPreview["phase"] {
  return value === "applied" || value === "aggregate" || value === "proposed"
    ? value
    : "proposed";
}

function readChangeType(value: unknown): EventDiffPreviewFile["changeType"] {
  return value === "add" ||
    value === "delete" ||
    value === "update" ||
    value === "move" ||
    value === "unknown"
    ? value
    : "unknown";
}

const NORMALIZED_TOOL_NAMES: Record<string, string> = {
  bash: "Bash",
  read: "Read",
  edit: "Edit",
  multiedit: "MultiEdit",
  write: "Write",
  notebookedit: "NotebookEdit",
  grep: "Grep",
  glob: "Glob",
  webfetch: "WebFetch",
  websearch: "WebSearch",
  task: "Task",
  agent: "Agent",
  todowrite: "TodoWrite",
  askuserquestion: "AskUserQuestion",
  question: "AskUserQuestion",
};

export function normalizeToolName(name: string | null | undefined): string | null {
  if (!name) return null;
  let normalized = name;
  if (normalized.startsWith("default_api:")) {
    normalized = normalized.slice("default_api:".length);
  }
  const lower = normalized.toLowerCase();
  if (lower in NORMALIZED_TOOL_NAMES) {
    return NORMALIZED_TOOL_NAMES[lower];
  }
  if (name.startsWith("default_api:") && normalized.length > 0) {
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
  }
  return normalized;
}

export function parseEvent(event: EventRecord): EventEnvelope {
  const metadata = event.metadata ?? {};
  const versionRaw = metadata.version;
  const version = typeof versionRaw === "number" ? versionRaw : null;

  const item: EventEnvelopeItem = {
    itemId: readString(metadata, "item_id"),
    itemType: readString(metadata, "item_type"),
    toolName: readString(metadata, "tool_name"),
    toolInput: readRecord(metadata, "tool_input"),
    toolUseId: readString(metadata, "tool_use_id"),
    payload: readRecord(metadata, "item"),
  };

  const approvalRaw =
    readRecord(metadata, "approval") ?? null;
  const approval: EventEnvelopeApproval | undefined = approvalRaw
    ? {
        approvalId: readString(approvalRaw, "approval_id"),
        toolName: readString(approvalRaw, "tool_name"),
        toolInput: readRecord(approvalRaw, "tool_input"),
        decisions: readStringArray(approvalRaw, "decisions"),
      }
    : undefined;

  return {
    version,
    kind: event.kind,
    text: event.text,
    status: readString(metadata, "status"),
    item,
    approval,
    diffPreview: readDiffPreview(metadata),
    extra: { ...metadata },
  };
}
