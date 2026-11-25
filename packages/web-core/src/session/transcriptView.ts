import type { EventRecord } from "../types";
import { isImportantEvent } from "./importance";
import { buildTranscriptItems, collapseSupersededPlanEvents } from "./transcript";
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

export function deriveTranscript(events: EventRecord[], density: TranscriptDensity): TranscriptView {
  const display = collapseSupersededPlanEvents(events);
  const visible = density === "full" ? display : display.filter(isImportantEvent);
  return {
    turns: groupTurns(buildTranscriptItems(visible)),
    hiddenCount: display.length - visible.length,
  };
}
