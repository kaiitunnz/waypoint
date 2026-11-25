"use client";

import { describeStep } from "@waypoint/web-core/activity";
import { cardKindOf } from "@waypoint/web-core/session/cardKind";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import { describeCall, summarizeToolRun } from "@waypoint/web-core/session/toolRun";
import { itemKey, primaryEvent, type LeafItem } from "@waypoint/web-core/session/transcript";

import { useSessionContext } from "../SessionContext";
import { CallRow } from "./CallRow";
import { ChangesNote } from "./ChangesNote";
import { DiffStat } from "./DiffStat";
import { RunLine } from "./RunLine";
import styles from "./ToolRun.module.css";

interface ToolRunProps {
  items: LeafItem[];
  // The session is working and this is its last run.
  working: boolean;
}

// A note that came in with the run: attached, resumed, a compaction, or a set of changes.
function RunNote({ child }: { child: LeafItem }) {
  if (child.kind !== "single") return null;
  const { event } = child;
  switch (cardKindOf(event)) {
    case "none":
      return null;
    case "diff_note":
      return <ChangesNote event={event} />;
    default:
      return (
        <div className={styles.note} title={event.text}>
          <span className={styles.noteText}>{event.text}</span>
          <time>{formatEventTime(event.ts)}</time>
        </div>
      );
  }
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
      phrase={step ?? summary.phrase}
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
