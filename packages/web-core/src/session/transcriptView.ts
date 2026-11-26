import type { EventRecord } from "../types";
import { isImportantEvent } from "./importance";
import { collapseRepeats } from "./repeats";
import { buildTranscriptItems, collapseSupersededPlanEvents, latestActionablePlanEvent } from "./transcript";
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
    turns: groupTurns(collapseRepeats(buildTranscriptItems(visible))),
    hiddenCount: display.length - visible.length,
  };
}

// The plan the dock asks the human to approve, if any.
export function dockedPlanEvent(events: EventRecord[]): EventRecord | null {
  return latestActionablePlanEvent(collapseSupersededPlanEvents(events));
}
