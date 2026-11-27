import { parseTaskNotification } from "../events";
import type { EventRecord } from "../types";
import { isImportantEvent } from "./importance";
import { collapseRepeats } from "./repeats";
import { buildTranscriptItems, collapseSupersededPlanEvents, type LeafItem, type TranscriptItem } from "./transcript";
import { groupTurns, type TranscriptTurn } from "./turns";

// Summary shows the important events and folds tool runs; Full shows every event
// and opens the runs.
export const TRANSCRIPT_DENSITIES = ["summary", "full"] as const;
export type TranscriptDensity = (typeof TRANSCRIPT_DENSITIES)[number];

export interface TranscriptView {
  turns: TranscriptTurn[];
  // Events Summary left out.
  hiddenCount: number;
}

// A reasoning step whose text is not shared leaves only a marker note.
const isReasoningMarker = (event: EventRecord) => event.kind === "system_note" && event.metadata.item_type === "reasoning";

// Reads every event, since Summary can hide a notification whose call it still shows.
function withReports(items: TranscriptItem[], events: EventRecord[]): TranscriptItem[] {
  const reports = new Map<string, EventRecord>();
  // A later notification may name only its task: it belongs to the last call that named the task.
  const callOfTask = new Map<string, string>();
  for (const event of events) {
    const note = parseTaskNotification(event);
    if (!note) continue;
    const callId = note.toolUseId ?? (note.taskId ? callOfTask.get(note.taskId) : undefined);
    if (!callId) continue;
    if (note.taskId) callOfTask.set(note.taskId, callId);
    reports.set(callId, event);
  }
  const attach = (item: LeafItem): LeafItem => {
    if (item.kind !== "pair") return item;
    const report = reports.get(item.pair.itemId);
    return report ? { ...item, pair: { ...item.pair, report } } : item;
  };
  return items.map((item) => ("items" in item ? { ...item, items: item.items.map(attach) } : attach(item)));
}

// The plan waiting for approval is shown above the composer, so the transcript
// leaves out the event at `dockedPlanSequence`.
export function deriveTranscript(
  events: EventRecord[],
  density: TranscriptDensity,
  dockedPlanSequence: number | null = null,
): TranscriptView {
  const display = collapseSupersededPlanEvents(events).filter(
    (event) => !isReasoningMarker(event) && event.sequence !== dockedPlanSequence,
  );
  const visible = density === "full" ? display : display.filter(isImportantEvent);
  return {
    turns: groupTurns(collapseRepeats(withReports(buildTranscriptItems(visible), events))),
    hiddenCount: display.length - visible.length,
  };
}
