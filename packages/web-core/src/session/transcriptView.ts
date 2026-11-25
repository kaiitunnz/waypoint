import type { EventRecord } from "../types";
import { isImportantEvent } from "./importance";
import { buildTranscriptItems, collapseSupersededPlanEvents } from "./transcript";
import { groupTurns, type TranscriptTurn } from "./turns";

// Summary shows the important events and folds tool runs; Full shows every event
// and opens the runs.
export type TranscriptDensity = "summary" | "full";

export interface TranscriptView {
  turns: TranscriptTurn[];
  // Events Summary left out.
  hiddenCount: number;
}

export interface TranscriptViewOptions {
  density: TranscriptDensity;
}

export function deriveTranscript(events: readonly EventRecord[], options: TranscriptViewOptions): TranscriptView {
  const display = collapseSupersededPlanEvents([...events]);
  const visible = options.density === "full" ? display : display.filter(isImportantEvent);
  return {
    turns: groupTurns(buildTranscriptItems(visible)),
    hiddenCount: display.length - visible.length,
  };
}
