"use client";

import { describeCall, liveCall, summarizeToolRun } from "@waypoint/web-core/session/toolRun";
import { itemKey, type LeafItem } from "@waypoint/web-core/session/transcript";

import { StepText } from "@/components/sessions/StepText";
import { SummaryText } from "@/components/ui/SummaryText";

import { useSessionContext } from "../SessionContext";
import { CallRow } from "./CallRow";
import { DiffStat } from "./DiffStat";
import { RunLine } from "./RunLine";
import { RunNote } from "./RunNote";

interface ToolRunProps {
  items: LeafItem[];
  working: boolean;
}

export function ToolRun({ items, working }: ToolRunProps) {
  const { cwd, full } = useSessionContext();
  const summary = summarizeToolRun(items);
  const calls = items.map((child) => ({ child, row: describeCall(child, cwd) }));
  const live = liveCall(items, working);

  return (
    <RunLine
      phrase={live?.step ? <StepText step={live.step} /> : summary.phrase}
      caption={live?.summary ? <SummaryText text={live.summary} /> : undefined}
      stats={
        summary.additions > 0 || summary.deletions > 0 ? (
          <DiffStat additions={summary.additions} deletions={summary.deletions} />
        ) : undefined
      }
      glyphs={summary.glyphs}
      defaultOpen={full}
      live={live !== null}
    >
      {calls.map(({ child, row }) => (row ? <CallRow key={row.key} row={row} /> : <RunNote key={itemKey(child)} child={child} />))}
    </RunLine>
  );
}
