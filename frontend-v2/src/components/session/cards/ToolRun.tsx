"use client";

import { describeStep } from "@waypoint/web-core/activity";
import { parseEvent } from "@waypoint/web-core/events";
import { cardKindOf } from "@waypoint/web-core/session/cardKind";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import { describeCall, summarizeToolRun } from "@waypoint/web-core/session/toolRun";
import type { TranscriptItem } from "@waypoint/web-core/session/transcript";

import { useSessionContext } from "../SessionContext";
import { CallRow } from "./CallRow";
import { ChangesNote } from "./ChangesNote";
import { RunLine } from "./RunLine";
import styles from "./ToolRun.module.css";

type RunChild = Extract<TranscriptItem, { kind: "single" | "pair" }>;

interface ToolRunProps {
  items: RunChild[];
  defaultOpen: boolean;
  // The session is working and this is its last run.
  working: boolean;
}

// A note that came in with the run: attached, resumed, a compaction, or a set of changes.
function RunNote({ child }: { child: RunChild }) {
  if (child.kind !== "single") return null;
  const kind = cardKindOf(child.event);
  if (kind === "none") return null;
  if (kind === "diff_note") {
    const preview = parseEvent(child.event).diffPreview;
    if (preview) return <ChangesNote event={child.event} preview={preview} defaultOpen={false} />;
  }
  return (
    <div className={styles.note} title={child.event.text}>
      <span className={styles.noteText}>{child.event.text}</span>
      <time>{formatEventTime(child.event.ts)}</time>
    </div>
  );
}

// A run of tool calls: what was done in plain words, and each call as one row.
export function ToolRun({ items, defaultOpen, working }: ToolRunProps) {
  const { cwd } = useSessionContext();
  const summary = summarizeToolRun(items);
  const calls = items.map((child) => ({ child, row: describeCall(child, cwd) }));
  const pending = calls.findLast(({ row }) => row?.status === "pending");
  const live = working && pending !== undefined;
  const step = live ? describeStep(pending.child.kind === "pair" ? (pending.child.pair.call ?? pending.child.event) : pending.child.event) : null;

  return (
    <RunLine
      phrase={step ?? summary.phrase}
      stats={
        summary.additions > 0 || summary.deletions > 0 ? (
          <>
            <span className={styles.add}>+{summary.additions}</span> <span className={styles.del}>−{summary.deletions}</span>
          </>
        ) : undefined
      }
      glyphs={summary.glyphs}
      defaultOpen={defaultOpen}
      live={live}
    >
      {calls.map(({ child, row }) =>
        row ? (
          <CallRow key={row.key} row={row} sources={child.kind === "pair" ? [child.pair.result, child.pair.call] : [child.event]} />
        ) : (
          <RunNote key={child.kind === "single" ? child.event.sequence : child.pair.itemId} child={child} />
        ),
      )}
    </RunLine>
  );
}
