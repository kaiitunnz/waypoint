import type { EventKind, EventRecord } from "./types";

export function ev(
  sequence: number,
  kind: EventKind,
  text = "",
  metadata: Record<string, unknown> = {},
  extra: Partial<EventRecord> = {},
): EventRecord {
  return {
    id: sequence,
    session_id: "s1",
    ts: `2026-01-01T00:00:${String(sequence).padStart(2, "0")}Z`,
    kind,
    text,
    metadata,
    sequence,
    ...extra,
  };
}

export function planMetadata(itemId: string, text = "the plan"): Record<string, unknown> {
  return { item_type: "plan", item_id: itemId, payload: { item: { text } } };
}
