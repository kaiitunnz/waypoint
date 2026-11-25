import { describe, expect, it } from "vitest";

import type { TaskNotificationView } from "../events";
import {
  reportUnavailableText,
  summarizeNotificationRun,
  taskNoteHeadline,
  taskStatusLamp,
  taskUsageParts,
} from "./taskNotificationView";

const view = (extra: Partial<TaskNotificationView> = {}): TaskNotificationView => ({
  id: "n",
  kind: "agent",
  status: null,
  summary: null,
  event: null,
  note: null,
  taskId: null,
  toolUseId: null,
  resultPreview: null,
  resultTruncated: false,
  eventTruncated: false,
  noteTruncated: false,
  outputAvailable: false,
  outputUnavailableReason: null,
  usage: null,
  ...extra,
});

describe("taskStatusLamp", () => {
  it("names the terminal state", () => {
    expect(taskStatusLamp(view({ status: "completed" }))).toEqual({ tone: "ok", label: "Completed" });
    expect(taskStatusLamp(view({ status: "Failed" }))).toEqual({ tone: "danger", label: "Failed" });
    expect(taskStatusLamp(view({ status: "stopped" }))).toEqual({ tone: "warn", label: "Stopped" });
    expect(taskStatusLamp(view({ status: "paused" }))).toEqual({ tone: "off", label: "paused" });
  });

  it("calls a monitor line an event", () => {
    expect(taskStatusLamp(view({ kind: "monitor", event: "x" }))).toEqual({ tone: "info", label: "Event" });
    expect(taskStatusLamp(view())).toBeNull();
  });
});

describe("taskUsageParts", () => {
  it("lists tokens, tool uses and duration", () => {
    expect(taskUsageParts({ subagentTokens: 12345, toolUses: 1, durationMs: 65_000 })).toEqual([
      "12,345 tokens",
      "1 tool use",
      "1m 5s",
    ]);
    expect(taskUsageParts({ subagentTokens: null, toolUses: 22, durationMs: null })).toEqual(["22 tool uses"]);
    expect(taskUsageParts(null)).toEqual([]);
  });

  it("scales a duration to seconds and minutes", () => {
    const duration = (durationMs: number) => taskUsageParts({ subagentTokens: null, toolUses: null, durationMs });
    expect(duration(4000)).toEqual(["4s"]);
    expect(duration(65_000)).toEqual(["1m 5s"]);
    expect(duration(120_000)).toEqual(["2m"]);
  });
});

describe("reportUnavailableText", () => {
  it("explains a missing report", () => {
    expect(reportUnavailableText(view({ outputAvailable: true }))).toBe("Report unavailable");
    expect(reportUnavailableText(view({ outputUnavailableReason: "full output not captured on import" }))).toBe(
      "Full report wasn't captured on import",
    );
    expect(reportUnavailableText(view({ outputUnavailableReason: "gone" }))).toBe("gone");
    expect(reportUnavailableText(view())).toBeNull();
  });
});

describe("taskNoteHeadline", () => {
  it("prefers the summary, then the event", () => {
    expect(taskNoteHeadline(view({ summary: "S", event: "E" }))).toBe("S");
    expect(taskNoteHeadline(view({ event: "E" }))).toBe("E");
    expect(taskNoteHeadline(view())).toBe("Task notification");
  });
});

describe("summarizeNotificationRun", () => {
  it("counts notes by what reported", () => {
    expect(summarizeNotificationRun(["agent", "agent", "background_command"])).toEqual({
      phrase: "Reports from 2 subagents, 1 background command",
      glyphs: [
        { glyph: "◇", count: 2 },
        { glyph: "›_", count: 1 },
      ],
    });
    expect(summarizeNotificationRun(["unknown"]).phrase).toBe("Reports from 1 task");
    expect(summarizeNotificationRun([]).phrase).toBe("Reports");
  });
});
