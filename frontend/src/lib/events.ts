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

export interface EventEnvelope {
  version: number | null;
  kind: EventRecord["kind"];
  text: string;
  status?: string | null;
  item?: EventEnvelopeItem;
  approval?: EventEnvelopeApproval;
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
    extra: { ...metadata },
  };
}
