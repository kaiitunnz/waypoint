import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import { deriveTranscript, dockedPlanEvent } from "./transcriptView";

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
  const withPlan = [ev(1, "user_input", "plan it"), ev(2, "agent_output", "plan", planMetadata("p1")), ev(3, "agent_output", "ready")];

  it("finds the undecided plan and leaves it out of the transcript", () => {
    const docked = dockedPlanEvent(withPlan);
    expect(docked?.sequence).toBe(2);
    const shown = deriveTranscript(withPlan, "full", docked?.sequence ?? null);
    const sequences = shown.turns.flatMap((turn) => JSON.stringify(turn).match(/"sequence":\d+/g) ?? []);
    expect(sequences).not.toContain('"sequence":2');
    expect(deriveTranscript(withPlan, "full").turns).not.toEqual(shown.turns);
  });
});
