"use client";

import { describeCall, liveCall, summarizeToolRun } from "@waypoint/web-core/session/toolRun";
import { itemKey, type LeafItem } from "@waypoint/web-core/session/transcript";

import { StepText } from "@/components/sessions/StepText";
import { SummaryHeading } from "@/components/ui/SummaryText";

import { useSessionContext } from "../SessionContext";
import { CallRow } from "./CallRow";
import { CommandLine } from "./CommandText";
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
  const step = live?.step ? <StepText step={live.step} /> : null;
  // A described call is headed by its summary, with what it runs under it.
  const heading = live?.summary ? <SummaryHeading text={live.summary} /> : null;
  const under = heading && (live?.command ? <CommandLine text={live.command} /> : step);

  return (
    <RunLine
      phrase={heading ?? step ?? summary.phrase}
      caption={under ?? undefined}
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
