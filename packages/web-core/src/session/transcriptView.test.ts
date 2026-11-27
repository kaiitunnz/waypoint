import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import { deriveTranscript } from "./transcriptView";

const events = [
  ev(1, "user_input", "go"),
  ev(2, "system_note", "heartbeat tick"),
  ev(3, "tool_call", "", { item_id: "a", tool_name: "Bash" }),
  ev(4, "tool_result", "ok", { item_id: "a" }),
  ev(5, "agent_output", "done"),
];

describe("deriveTranscript", () => {
  it("hides low-signal notes in Summary and counts them", () => {
    const view = deriveTranscript(events, "summary");
    expect(view.hiddenCount).toBe(1);
    expect(view.turns.map((turn) => turn.kind)).toEqual(["user", "agent"]);
  });

  it("leaves out the marker of an unshared reasoning step, which is not counted as hidden", () => {
    const marker = ev(6, "system_note", "Reasoning", { item_type: "reasoning", visibility: "detail" });
    for (const density of ["summary", "full"] as const) {
      const view = deriveTranscript([...events, marker], density);
      expect(JSON.stringify(view.turns)).not.toContain("Reasoning");
    }
    expect(deriveTranscript([...events, marker], "summary").hiddenCount).toBe(1);
  });

  it("shows every event in Full", () => {
    const view = deriveTranscript(events, "full");
    expect(view.hiddenCount).toBe(0);
    expect(view.turns.map((turn) => turn.kind)).toEqual(["user", "rule", "agent"]);
  });
});

describe("docked plan", () => {
  it("leaves the docked plan out of the transcript", () => {
    const events = [ev(1, "user_input", "plan it"), ev(2, "agent_output", "plan", planMetadata("p1"))];
    const sequences = (sequence: number | null) =>
      JSON.stringify(deriveTranscript(events, "full", sequence).turns).match(/"sequence":\d+/g) ?? [];
    expect(sequences(null)).toContain('"sequence":2');
    expect(sequences(2)).not.toContain('"sequence":2');
  });
});

describe("task notification reports", () => {
  const notification = (sequence: number, callId: string | null, text: string, taskId?: string) =>
    ev(sequence, "system_note", text, {
      method: "task_notification",
      visibility: "detail",
      task_notification: { version: 1, id: text, kind: "agent", tool_use_id: callId, task_id: taskId },
    });
  const launch = (sequence: number, id: string) => [
    ev(sequence, "tool_call", "", { item_id: id, tool_name: "Agent" }),
    ev(sequence + 1, "tool_result", "launched", { item_id: id }),
  ];
  const reportsOf = (events: ReturnType<typeof ev>[], density: "summary" | "full") => {
    const reports: Record<string, string | undefined> = {};
    for (const turn of deriveTranscript(events, density).turns) {
      if (turn.kind !== "agent") continue;
      for (const item of turn.items) {
        for (const leaf of item.kind === "pair" ? [item] : "items" in item ? item.items : []) {
          if (leaf.kind === "pair") reports[leaf.pair.itemId] = leaf.pair.report?.text;
        }
      }
    }
    return reports;
  };

  it("gives a call the latest notification about it, at either density", () => {
    const events = [
      ...launch(1, "a"),
      ...launch(3, "b"),
      ...launch(5, "c"),
      notification(7, "a", "first"),
      notification(8, "a", "second"),
      notification(9, "c", "stray"),
    ];
    for (const density of ["summary", "full"] as const) {
      expect(reportsOf(events, density)).toEqual({ a: "second", b: undefined, c: "stray" });
    }
  });

  it("follows a task to a later notification that names no call", () => {
    const events = [
      ...launch(1, "a"),
      ...launch(3, "b"),
      notification(5, "a", "interim", "t1"),
      notification(6, "b", "other", "t2"),
      notification(7, null, "final", "t1"),
      notification(8, null, "unknown task", "t3"),
      notification(9, null, "no task"),
    ];
    expect(reportsOf(events, "summary")).toEqual({ a: "final", b: "other" });
  });

  it("leaves a follow-up's reports to the call that asked for them", () => {
    const events = [
      ...launch(1, "a"),
      notification(3, "a", "first", "t1"),
      ...launch(4, "send"),
      notification(6, "send", "second", "t1"),
      notification(7, null, "third", "t1"),
    ];
    expect(reportsOf(events, "summary")).toEqual({ a: "first", send: "third" });
  });
});
