import type { EventRecord } from "../types";
import { cardKindOf } from "./cardKind";
import type { TranscriptItem } from "./transcript";

// A system note counts its repeats; a change note is a snapshot, so one copy says it all.
type RepeatKind = "rule" | "diff_note";

function sameNote(a: EventRecord, b: EventRecord, kind: RepeatKind): boolean {
  if (a.text !== b.text) return false;
  return kind === "rule" || JSON.stringify(a.metadata.diff_preview) === JSON.stringify(b.metadata.diff_preview);
}

function collapse<T extends TranscriptItem>(items: readonly T[]): T[] {
  const out: T[] = [];
  // Where the note the next item may repeat sits. Items that render nothing leave it there.
  let last = -1;
  for (const item of items) {
    if (item.kind !== "single") {
      last = -1;
      out.push(item);
      continue;
    }
    const kind = cardKindOf(item.event);
    if (kind === "none") {
      out.push(item);
      continue;
    }
    if (kind !== "rule" && kind !== "diff_note") {
      last = -1;
      out.push(item);
      continue;
    }
    const previous = out[last];
    if (previous?.kind === "single" && cardKindOf(previous.event) === kind && sameNote(previous.event, item.event, kind)) {
      if (kind === "rule") out[last] = { ...previous, repeat: { count: (previous.repeat?.count ?? 1) + 1, until: item.event.ts } };
      continue;
    }
    last = out.push(item) - 1;
  }
  return out;
}

// Folds identical notes that follow one another, at the top level and inside runs.
export function collapseRepeats(items: readonly TranscriptItem[]): TranscriptItem[] {
  return collapse(items).map((item) => ("items" in item ? { ...item, items: collapse(item.items) } : item));
}
