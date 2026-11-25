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
  // Whether any tool or notification run exists, for the density toggle.
  hasRuns: boolean;
}

export interface TranscriptViewOptions {
  density: TranscriptDensity;
  // An event another surface shows instead, such as the pending plan approval.
  excludeEvent?: (event: EventRecord) => boolean;
}

export function deriveTranscript(events: readonly EventRecord[], options: TranscriptViewOptions): TranscriptView {
  const display = collapseSupersededPlanEvents([...events]);
  const visible = options.density === "full" ? display : display.filter(isImportantEvent);
  const shown = options.excludeEvent ? visible.filter((event) => !options.excludeEvent?.(event)) : visible;
  const items = buildTranscriptItems(shown);
  return {
    turns: groupTurns(items),
    hiddenCount: display.length - visible.length,
    hasRuns: items.some((item) => item.kind === "tool_run" || item.kind === "notification_run"),
  };
}
