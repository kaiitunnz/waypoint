import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
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

  it("shows every event in Full", () => {
    const view = deriveTranscript(events, "full");
    expect(view.hiddenCount).toBe(0);
    expect(view.turns.map((turn) => turn.kind)).toEqual(["user", "rule", "agent"]);
  });
});
