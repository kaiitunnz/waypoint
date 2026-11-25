"use client";

import { describeStep } from "@waypoint/web-core/activity";
import { describeCall, summarizeToolRun } from "@waypoint/web-core/session/toolRun";
import { itemKey, primaryEvent, type LeafItem } from "@waypoint/web-core/session/transcript";

import { StepText } from "@/components/sessions/StepText";

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
  const pending = calls.findLast(({ row }) => row?.status === "pending");
  const live = working && pending !== undefined;
  const step = live ? describeStep(primaryEvent(pending.child)) : null;

  return (
    <RunLine
      phrase={step ? <StepText step={step} /> : summary.phrase}
      stats={
        summary.additions > 0 || summary.deletions > 0 ? (
          <DiffStat additions={summary.additions} deletions={summary.deletions} />
        ) : undefined
      }
      glyphs={summary.glyphs}
      defaultOpen={full}
      live={live}
    >
      {calls.map(({ child, row }) => (row ? <CallRow key={row.key} row={row} /> : <RunNote key={itemKey(child)} child={child} />))}
    </RunLine>
  );
}
