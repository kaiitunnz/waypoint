import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import {
  buildTranscriptItems,
  collapseSupersededPlanEvents,
  foldOlderEvents,
  itemKey,
  latestActionablePlanEvent,
  mergeEvents,
  minRawSequence,
  primaryEvent,
  removeConfirmedOptimisticMessages,
  sanitizeEvent,
  type TranscriptItem,
} from "./transcript";

function shape(items: TranscriptItem[]): unknown[] {
  return items.map((item) => {
    switch (item.kind) {
      case "single":
        return `single:${item.event.sequence}`;
      case "pair":
        return `pair:${item.pair.itemId}`;
      default:
        return { [item.kind]: shape(item.items) };
    }
  });
}

const call = (seq: number, id: string, tool = "Bash", text = "call") =>
  ev(seq, "tool_call", text, { item_id: id, tool_name: tool });
const result = (seq: number, id: string, text = "out", extra: Record<string, unknown> = {}) =>
  ev(seq, "tool_result", text, { item_id: id, ...extra });

describe("buildTranscriptItems", () => {
  it("returns an empty list for no events", () => {
    expect(buildTranscriptItems([])).toEqual([]);
  });

  it("keeps content events as standalone singles", () => {
    const items = buildTranscriptItems([
      ev(1, "user_input", "hi"),
      ev(2, "agent_output", "hello"),
      ev(3, "approval_request", "ok?"),
    ]);
    expect(shape(items)).toEqual(["single:1", "single:2", "single:3"]);
  });

  it("pairs a call with its result into a single tool run entry", () => {
    const [run] = buildTranscriptItems([call(1, "a"), result(2, "a")]);
    expect(run.kind).toBe("tool_run");
    if (run.kind !== "tool_run") return;
    expect(run.items).toHaveLength(1);
    const item = run.items[0];
    expect(item.kind).toBe("pair");
    if (item.kind !== "pair") return;
    expect(item.pair.call?.sequence).toBe(1);
    expect(item.pair.result?.sequence).toBe(2);
    expect(item.pair.ts).toBe("2026-01-01T00:00:02Z");
    expect(item.pair.sequence).toBe(2);
  });

  it("pairs a result that arrives before its call", () => {
    const [run] = buildTranscriptItems([result(5, "a"), call(3, "a")]);
    if (run.kind !== "tool_run") throw new Error("expected a tool run");
    const item = run.items[0];
    if (item.kind !== "pair") throw new Error("expected a pair");
    expect(item.pair.call?.sequence).toBe(3);
    expect(item.pair.result?.sequence).toBe(5);
    expect(item.pair.sequence).toBe(5);
    expect(item.pair.ts).toBe("2026-01-01T00:00:03Z");
  });

  it("merges repeated results for one item id", () => {
    const [run] = buildTranscriptItems([
      call(1, "a"),
      result(2, "a", "line1", { method: "item/commandExecution/outputDelta" }),
      result(3, "a", "line2", { method: "item/commandExecution/outputDelta" }),
    ]);
    if (run.kind !== "tool_run") throw new Error("expected a tool run");
    const item = run.items[0];
    if (item.kind !== "pair") throw new Error("expected a pair");
    expect(item.pair.result?.text).toBe("line1line2");
    expect(item.pair.result?.sequence).toBe(3);
  });

  it("treats tool events without an item id as singles that still join a run", () => {
    const items = buildTranscriptItems([
      ev(1, "tool_call", "x", { tool_name: "Bash" }),
      ev(2, "tool_result", "y"),
    ]);
    expect(shape(items)).toEqual([{ tool_run: ["single:1", "single:2"] }]);
  });

  it("groups consecutive tool pairs and splits runs at content", () => {
    const items = buildTranscriptItems([
      call(1, "a"),
      result(2, "a"),
      call(3, "b"),
      ev(4, "agent_output", "thinking"),
      call(5, "c"),
    ]);
    expect(shape(items)).toEqual([
      { tool_run: ["pair:a", "pair:b"] },
      "single:4",
      { tool_run: ["pair:c"] },
    ]);
  });

  it("absorbs lifecycle notes into an active run but lets them stand alone otherwise", () => {
    const items = buildTranscriptItems([
      ev(1, "system_note", "started"),
      call(2, "a"),
      ev(3, "system_note", "note in the middle"),
      call(4, "b"),
      ev(5, "agent_output", "done"),
      ev(6, "status_update", "idle"),
    ]);
    expect(shape(items)).toEqual([
      "single:1",
      { tool_run: ["pair:a", "single:3", "pair:b"] },
      "single:5",
      "single:6",
    ]);
  });

  it("keeps AskUserQuestion and SendUserFile calls out of tool runs", () => {
    const items = buildTranscriptItems([
      call(1, "a"),
      call(2, "q", "AskUserQuestion"),
      result(3, "q"),
      call(4, "f", "SendUserFile"),
      call(5, "b"),
    ]);
    expect(shape(items)).toEqual([
      { tool_run: ["pair:a"] },
      "pair:q",
      "pair:f",
      { tool_run: ["pair:b"] },
    ]);
  });

  it("normalises tool name aliases before deciding a card is standalone", () => {
    const items = buildTranscriptItems([call(1, "q", "default_api:askuserquestion")]);
    expect(shape(items)).toEqual(["pair:q"]);
  });

  it("keeps a model switch standalone and absorbs a launch-time mismatch", () => {
    const items = buildTranscriptItems([
      call(1, "a"),
      ev(2, "system_note", "switched", { method: "model.change", reason: "switch" }),
      call(3, "b"),
      ev(4, "system_note", "mismatch", { method: "model.change", reason: "initial_mismatch" }),
    ]);
    expect(shape(items)).toEqual([
      { tool_run: ["pair:a"] },
      "single:2",
      { tool_run: ["pair:b", "single:4"] },
    ]);
  });

  it("batches task notifications into their own run and alternates with tool runs", () => {
    const note = (seq: number) =>
      ev(seq, "system_note", "task", { method: "task_notification" });
    const items = buildTranscriptItems([
      note(1),
      note(2),
      call(3, "a"),
      note(4),
      ev(5, "agent_output", "x"),
    ]);
    expect(shape(items)).toEqual([
      { notification_run: ["single:1", "single:2"] },
      { tool_run: ["pair:a"] },
      { notification_run: ["single:4"] },
      "single:5",
    ]);
  });

  it("lets lifecycle noise join an open notification run", () => {
    const items = buildTranscriptItems([
      ev(1, "system_note", "task", { method: "claude.task_notification" }),
      ev(2, "system_note", "resumed"),
    ]);
    expect(shape(items)).toEqual([{ notification_run: ["single:1", "single:2"] }]);
  });

  it("keeps plan events as content that terminates a run", () => {
    const items = buildTranscriptItems([
      call(1, "a"),
      ev(2, "agent_output", "plan", planMetadata("p1")),
      ev(3, "system_note", "later"),
    ]);
    expect(shape(items)).toEqual([{ tool_run: ["pair:a"] }, "single:2", "single:3"]);
  });
});

describe("mergeEvents", () => {
  it("ignores an event whose id or sequence is already present", () => {
    const current = [ev(1, "agent_output", "a", { item_id: "i" })];
    expect(mergeEvents(current, ev(1, "agent_output", "b"))).toBe(current);
    const sameSequence = ev(9, "agent_output", "c", {}, { sequence: 1 });
    expect(mergeEvents(current, sameSequence)).toBe(current);
    const sameId = ev(9, "agent_output", "d", {}, { id: 1 });
    expect(mergeEvents(current, sameId)).toBe(current);
  });

  it("appends events without an item id", () => {
    const merged = mergeEvents([ev(1, "agent_output", "a")], ev(2, "agent_output", "b"));
    expect(merged.map((e) => e.text)).toEqual(["a", "b"]);
  });

  it("concatenates agent_output deltas that share an item id and advances ts and sequence", () => {
    const merged = mergeEvents(
      [ev(1, "agent_output", "Hi ", { item_id: "i", a: 1 })],
      ev(4, "agent_output", "there", { item_id: "i", b: 2 }),
    );
    expect(merged).toHaveLength(1);
    expect(merged[0].text).toBe("Hi there");
    expect(merged[0].sequence).toBe(4);
    expect(merged[0].ts).toBe("2026-01-01T00:00:04Z");
    expect(merged[0].metadata).toEqual({ item_id: "i", a: 1, b: 2 });
  });

  it("does not merge across kinds even with the same item id", () => {
    const merged = mergeEvents(
      [ev(1, "agent_output", "a", { item_id: "i" })],
      ev(2, "tool_result", "b", { item_id: "i" }),
    );
    expect(merged).toHaveLength(2);
  });

  it("lets later metadata win on key collisions", () => {
    const merged = mergeEvents(
      [ev(1, "agent_output", "a", { item_id: "i", k: "old" })],
      ev(2, "agent_output", "b", { item_id: "i", k: "new" }),
    );
    expect(merged[0].metadata.k).toBe("new");
  });

  describe("tool_result text", () => {
    const merge = (existing: ReturnType<typeof ev>, incoming: ReturnType<typeof ev>) =>
      mergeEvents([existing], incoming)[0].text;
    const delta = { item_id: "i", method: "item/fileChange/outputDelta" };

    it("appends a delta onto existing text", () => {
      expect(merge(ev(1, "tool_result", "a", delta), ev(2, "tool_result", "b", delta))).toBe("ab");
      expect(
        merge(ev(1, "tool_result", "a", { item_id: "i" }), ev(2, "tool_result", "b", delta)),
      ).toBe("ab");
    });

    it("keeps an accumulated delta over a later snapshot", () => {
      expect(
        merge(ev(1, "tool_result", "acc", delta), ev(2, "tool_result", "final", { item_id: "i" })),
      ).toBe("acc");
      expect(
        merge(ev(1, "tool_result", "", delta), ev(2, "tool_result", "final", { item_id: "i" })),
      ).toBe("final");
    });

    it("replaces todo lists instead of appending", () => {
      const todo = { item_id: "i", item_type: "todo_list" };
      expect(merge(ev(1, "tool_result", "old", todo), ev(2, "tool_result", "new", todo))).toBe("new");
      expect(merge(ev(1, "tool_result", "old", todo), ev(2, "tool_result", "", todo))).toBe("old");
    });

    it("takes the incoming text when nothing exists and keeps identical text once", () => {
      const meta = { item_id: "i" };
      expect(merge(ev(1, "tool_result", "", meta), ev(2, "tool_result", "x", meta))).toBe("x");
      expect(merge(ev(1, "tool_result", "x", meta), ev(2, "tool_result", "x", meta))).toBe("x");
      expect(merge(ev(1, "tool_result", "x", meta), ev(2, "tool_result", "", meta))).toBe("x");
    });

    it("joins distinct snapshots with a newline unless one side already has it", () => {
      const meta = { item_id: "i" };
      expect(merge(ev(1, "tool_result", "a", meta), ev(2, "tool_result", "b", meta))).toBe("a\nb");
      expect(merge(ev(1, "tool_result", "a\n", meta), ev(2, "tool_result", "b", meta))).toBe("a\nb");
      expect(merge(ev(1, "tool_result", "a", meta), ev(2, "tool_result", "\nb", meta))).toBe("a\nb");
    });
  });
});

describe("foldOlderEvents", () => {
  it("prepends older standalone events in sequence order", () => {
    const current = [ev(10, "user_input", "now")];
    const folded = foldOlderEvents(current, [ev(1, "user_input", "a"), ev(2, "user_input", "b")]);
    expect(folded.map((e) => e.sequence)).toEqual([1, 2, 10]);
  });

  it("drops older events already present by id or sequence", () => {
    const current = [ev(10, "user_input", "now")];
    const folded = foldOlderEvents(current, [
      ev(1, "user_input", "dup id", {}, { id: 10 }),
      ev(10, "user_input", "dup seq", {}, { id: 99 }),
      ev(2, "user_input", "new"),
    ]);
    expect(folded.map((e) => e.sequence)).toEqual([2, 10]);
  });

  it("folds older deltas of a visible item in front of its current text", () => {
    const current = [ev(10, "agent_output", "c", { item_id: "i" })];
    const folded = foldOlderEvents(current, [
      ev(1, "agent_output", "a", { item_id: "i" }),
      ev(2, "agent_output", "b", { item_id: "i" }),
    ]);
    expect(folded).toHaveLength(1);
    expect(folded[0].text).toBe("abc");
    expect(folded[0].sequence).toBe(10);
  });

  it("keeps older tool_result deltas over the current snapshot, as a forward merge would", () => {
    const delta = { item_id: "i", method: "item/fileChange/outputDelta" };
    const folded = foldOlderEvents(
      [ev(10, "tool_result", "final", { item_id: "i" })],
      [ev(1, "tool_result", "a", delta), ev(2, "tool_result", "b", delta)],
    );
    expect(folded).toHaveLength(1);
    expect(folded[0].text).toBe("ab");
  });

  it("coalesces older events that fold into nothing visible among themselves", () => {
    const folded = foldOlderEvents(
      [ev(10, "user_input", "now")],
      [ev(1, "agent_output", "a", { item_id: "x" }), ev(2, "agent_output", "b", { item_id: "x" })],
    );
    expect(folded.map((e) => e.text)).toEqual(["ab", "now"]);
  });

  it("leaves the list unchanged for no older events", () => {
    const current = [ev(10, "user_input", "now")];
    expect(foldOlderEvents(current, [])).toEqual(current);
  });
});

describe("minRawSequence", () => {
  it("is null for an empty list and the smallest sequence otherwise", () => {
    expect(minRawSequence([])).toBeNull();
    expect(minRawSequence([ev(7, "user_input"), ev(3, "user_input"), ev(5, "user_input")])).toBe(3);
  });
});

describe("collapseSupersededPlanEvents", () => {
  it("keeps only the latest plan event per item id", () => {
    const first = ev(1, "agent_output", "p", planMetadata("p1", "v1"));
    const second = ev(3, "agent_output", "p", planMetadata("p1", "v2"));
    const other = ev(2, "agent_output", "o", planMetadata("p2"));
    const text = ev(4, "agent_output", "plain");
    expect(collapseSupersededPlanEvents([first, other, second, text])).toEqual([other, second, text]);
  });

  it("returns the same array when there are no plan events", () => {
    const events = [ev(1, "agent_output", "plain")];
    expect(collapseSupersededPlanEvents(events)).toBe(events);
  });
});

describe("latestActionablePlanEvent", () => {
  const plan = (seq: number, id: string) => ev(seq, "agent_output", "p", planMetadata(id));
  const decision = (seq: number, id: string) =>
    ev(seq, "system_note", "d", { plan_item_id: id, plan_decision: "approve" });

  it("is null without a plan", () => {
    expect(latestActionablePlanEvent([ev(1, "agent_output", "x")])).toBeNull();
  });

  it("returns the newest undecided plan", () => {
    const events = [plan(1, "p1"), ev(2, "agent_output", "x")];
    expect(latestActionablePlanEvent(events)).toBe(events[0]);
  });

  it("is null once that plan has a decision after it", () => {
    expect(latestActionablePlanEvent([plan(1, "p1"), decision(2, "p1")])).toBeNull();
  });

  it("returns a newer plan even though an older one was decided", () => {
    const events = [plan(1, "p1"), decision(2, "p1"), plan(3, "p2")];
    expect(latestActionablePlanEvent(events)).toBe(events[2]);
  });

  it("is not hidden by a decision that belongs to a different plan", () => {
    const events = [plan(1, "p1"), decision(2, "p2")];
    expect(latestActionablePlanEvent(events)).toBe(events[0]);
  });

  it("only looks at the newest plan, so a newer decided plan hides an older open one", () => {
    expect(latestActionablePlanEvent([plan(1, "p1"), plan(2, "p2"), decision(3, "p2")])).toBeNull();
  });

  it("ignores approval requests that carry a plan", () => {
    const approval = ev(1, "approval_request", "ExitPlanMode", {
      tool_name: "ExitPlanMode",
      approval_id: "a1",
      tool_input: { plan: "Do it" },
    });
    expect(latestActionablePlanEvent([approval])).toBeNull();
  });

  it("ignores a decision note that lacks a decision value", () => {
    const events = [plan(1, "p1"), ev(2, "system_note", "d", { plan_item_id: "p1" })];
    expect(latestActionablePlanEvent(events)).toBe(events[0]);
  });
});

describe("removeConfirmedOptimisticMessages", () => {
  const msg = (text: string) => ({ text });

  it("returns the same array when nothing is confirmed", () => {
    const messages = [msg("a")];
    expect(removeConfirmedOptimisticMessages(messages, [])).toBe(messages);
  });

  it("removes one optimistic message per confirmed text", () => {
    const messages = [msg("a"), msg("a"), msg("b")];
    expect(removeConfirmedOptimisticMessages(messages, ["a"])).toEqual([msg("a"), msg("b")]);
    expect(removeConfirmedOptimisticMessages(messages, ["a", "a", "a"])).toEqual([msg("b")]);
  });

  it("ignores confirmations that match nothing", () => {
    const messages = [msg("a")];
    expect(removeConfirmedOptimisticMessages(messages, ["zzz"])).toEqual(messages);
  });
});

describe("sanitizeEvent", () => {
  it("strips ANSI escapes from the text and nothing else", () => {
    const event = ev(1, "tool_result", "\u001b[31mred\u001b[0m", { a: 1 });
    const clean = sanitizeEvent(event);
    expect(clean.text).toBe("red");
    expect(clean.metadata).toBe(event.metadata);
    expect(clean).not.toBe(event);
  });
});

describe("itemKey", () => {
  it("survives a streamed delta merged into the first event", () => {
    const first = buildTranscriptItems([ev(1, "agent_output", "Hi ", { item_id: "i" })])[0];
    const merged = buildTranscriptItems(mergeEvents([ev(1, "agent_output", "Hi ", { item_id: "i" })], ev(4, "agent_output", "there", { item_id: "i" })))[0];
    expect(itemKey(merged)).toBe(itemKey(first));
  });

  it("names a pair by its item id and a run by its first entry", () => {
    const [item] = buildTranscriptItems([call(1, "a"), ev(2, "tool_result", "", { item_id: "a" }), call(3, "b")]);
    if (item.kind !== "tool_run") throw new Error("expected a tool run");
    expect(itemKey(item.items[0])).toBe("pair-a");
    expect(itemKey(item)).toBe("tool_run-pair-a");
  });
});

describe("primaryEvent", () => {
  const leaf = (events: ReturnType<typeof ev>[]) => {
    const [run] = buildTranscriptItems(events);
    if (run.kind !== "tool_run") throw new Error("expected a tool run");
    return run.items[0];
  };

  it("is the call of a pair, else its result", () => {
    expect(primaryEvent(leaf([call(1, "a"), ev(2, "tool_result", "", { item_id: "a" })])).sequence).toBe(1);
    expect(primaryEvent(leaf([ev(2, "tool_result", "", { item_id: "z" })])).sequence).toBe(2);
  });
});
