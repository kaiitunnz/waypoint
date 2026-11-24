import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import { isImportantEvent } from "./importance";

describe("isImportantEvent", () => {
  it.each(["user_input", "agent_output", "tool_call", "tool_result", "approval_request"] as const)(
    "keeps %s events",
    (kind) => {
      expect(isImportantEvent(ev(1, kind, "anything"))).toBe(true);
    },
  );

  it("hides raw terminal chunks", () => {
    expect(isImportantEvent(ev(1, "raw_terminal_chunk", "failed error"))).toBe(false);
  });

  it("lets a backend visibility verdict override every other rule", () => {
    expect(isImportantEvent(ev(1, "user_input", "hi", { visibility: "detail" }))).toBe(false);
    expect(isImportantEvent(ev(1, "tool_result", "", { visibility: "detail" }))).toBe(false);
    expect(isImportantEvent(ev(1, "system_note", "quiet", { visibility: "important" }))).toBe(true);
    expect(isImportantEvent(ev(1, "raw_terminal_chunk", "", { visibility: "important" }))).toBe(true);
  });

  it("ignores unknown visibility values", () => {
    expect(isImportantEvent(ev(1, "system_note", "quiet", { visibility: "other" }))).toBe(false);
    expect(isImportantEvent(ev(1, "user_input", "hi", { visibility: "other" }))).toBe(true);
  });

  describe("system notes and status updates", () => {
    it.each(["system_note", "status_update"] as const)("%s: drops plain notes", (kind) => {
      expect(isImportantEvent(ev(1, kind, "heartbeat"))).toBe(false);
      expect(isImportantEvent(ev(1, kind, ""))).toBe(false);
    });

    it.each([
      "Approval response sent",
      "approval timed out",
      "files attached",
      "Session started",
      "Session terminated",
      "interrupting turn",
      "resume requested",
      "request failed",
      "an Error occurred",
      "process exited",
      "context compacted",
    ])("keeps a note saying %j", (text) => {
      expect(isImportantEvent(ev(1, "system_note", text))).toBe(true);
    });

    it("matches keywords case-insensitively and as substrings", () => {
      expect(isImportantEvent(ev(1, "status_update", "STARTED"))).toBe(true);
      expect(isImportantEvent(ev(1, "status_update", "unstartedly"))).toBe(true);
    });

    it("keeps approval invalidations", () => {
      expect(isImportantEvent(ev(1, "system_note", "x", { method: "approval.invalidated" }))).toBe(true);
    });

    it("keeps built-in command results", () => {
      expect(isImportantEvent(ev(1, "system_note", "x", { builtin_command: "compact" }))).toBe(true);
      expect(isImportantEvent(ev(1, "system_note", "x", { builtin_command: 3 }))).toBe(false);
    });

    it("keeps plan notes", () => {
      expect(isImportantEvent(ev(1, "system_note", "", planMetadata("p1")))).toBe(true);
    });

    it("keeps task notifications whatever they say", () => {
      expect(isImportantEvent(ev(1, "system_note", "finished", { method: "task_notification" }))).toBe(true);
      expect(isImportantEvent(ev(1, "system_note", "ok", { method: "claude.task_notification" }))).toBe(true);
    });

    it("keeps a model switch but not a launch-time mismatch", () => {
      const change = (reason: string) =>
        ev(1, "system_note", "model failed", { method: "model.change", reason });
      expect(isImportantEvent(change("switch"))).toBe(true);
      expect(isImportantEvent(change("initial_mismatch"))).toBe(false);
    });
  });
});
