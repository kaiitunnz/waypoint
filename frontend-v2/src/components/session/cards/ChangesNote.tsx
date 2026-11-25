"use client";

import { parseEvent } from "@waypoint/web-core/events";
import { formatEventTime } from "@waypoint/web-core/session/cardText";
import type { EventRecord } from "@waypoint/web-core/types";

import { DiffBlock } from "./DiffBlock";
import { DiffStat } from "./DiffStat";
import { RunLine } from "./RunLine";

export function ChangesNote({ event, defaultOpen }: { event: EventRecord; defaultOpen: boolean }) {
  const preview = parseEvent(event).diffPreview;
  if (!preview) return null;
  return (
    <RunLine
      defaultOpen={defaultOpen}
      phrase={event.text || "Changes"}
      stats={<DiffStat additions={preview.totalAdditions} deletions={preview.totalDeletions} />}
      trailing={<time>{formatEventTime(event.ts)}</time>}
    >
      <DiffBlock preview={preview} headers />
    </RunLine>
  );
}
