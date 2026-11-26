import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { collapseRepeats } from "./repeats";
import { buildTranscriptItems, type TranscriptItem } from "./transcript";

const restored = (sequence: number) => ev(sequence, "system_note", "Session restored");
const changes = (sequence: number, added: number) =>
  ev(sequence, "system_note", `Turn changes: +${added}`, {
    diff_preview: { files: [{ path: "a", diff: "+x" }], total_additions: added, total_deletions: 0 },
  });
const call = (sequence: number, id: string) => ev(sequence, "tool_call", "", { item_id: id, tool_name: "Bash" });

const collapse = (...events: ReturnType<typeof ev>[]) => collapseRepeats(buildTranscriptItems(events));

const summarize = (items: TranscriptItem[]): unknown[] =>
  items.map((item) => {
    if (item.kind === "tool_run") return summarize(item.items);
    if (item.kind !== "single") return item.kind;
    return item.repeat ? `${item.event.sequence}x${item.repeat.count}` : item.event.sequence;
  });

describe("collapseRepeats", () => {
  it("folds identical system notes into the first, with the count and the last time", () => {
    const items = collapse(restored(1), restored(2), restored(3));
    expect(summarize(items)).toEqual(["1x3"]);
    expect(items[0]).toMatchObject({ repeat: { count: 3, until: restored(3).ts } });
  });

  it("keeps notes that differ or have something between them", () => {
    expect(summarize(collapse(restored(1), ev(2, "system_note", "Session attached"), restored(3)))).toEqual([1, 2, 3]);
    expect(summarize(collapse(restored(1), ev(2, "agent_output", "hi"), restored(3)))).toEqual([1, 2, 3]);
  });

  it("looks past items that render nothing", () => {
    expect(summarize(collapse(restored(1), ev(2, "agent_output", "  "), restored(3)))).toEqual(["1x2", 2]);
  });

  it("keeps one of each run of identical change notes, without a count", () => {
    expect(summarize(collapse(changes(1, 2), changes(2, 2), changes(3, 5)))).toEqual([1, 3]);
  });

  it("folds notes inside a tool run, where a call separates them", () => {
    expect(summarize(collapse(call(1, "a"), changes(2, 2), changes(3, 2), call(4, "b"), changes(5, 2)))).toEqual([["pair", 2, "pair", 5]]);
  });
});
