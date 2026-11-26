import { describe, expect, it } from "vitest";

import type { EventRecord } from "../types";
import { modelNoticeFromEvent } from "./modelNotice";

const event = (metadata: Record<string, unknown>): EventRecord =>
  ({ id: 1, sequence: 1, kind: "system_note", text: "", ts: "2026-01-01T00:00:00Z", metadata }) as EventRecord;

describe("modelNoticeFromEvent", () => {
  it("reads a switch", () => {
    expect(
      modelNoticeFromEvent(event({ method: "model.change", reason: "switch", previous_model: "a", current_model: "b" })),
    ).toEqual({ reason: "switch", previous: "a", current: "b", selection: null });
  });

  it("reads anything else as a launch mismatch", () => {
    expect(modelNoticeFromEvent(event({ method: "model.change", current_model: "b", selection: "a" }))).toEqual({
      reason: "initial_mismatch",
      previous: null,
      current: "b",
      selection: "a",
    });
  });

  it("ignores other events", () => {
    expect(modelNoticeFromEvent(event({ method: "turn.started" }))).toBeNull();
  });
});
