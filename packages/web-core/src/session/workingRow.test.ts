import { describe, expect, it, vi } from "vitest";

import { ev } from "../test-helpers";
import type { EventKind, EventRecord } from "../types";
import { deriveTranscript } from "./transcriptView";
import { findTurnStart, formatElapsed, turnStart, workingStep } from "./workingRow";

// `ev` stamps an event at as many seconds as its sequence.
const at = (sequence: number) => Date.parse(ev(sequence, "system_note").ts);

const event = (sequence: number, kind: EventKind, status: string, extra: Record<string, unknown> = {}, text = "x") =>
  ev(sequence, kind, text, { status, ...extra });

const message = (sequence: number, status = "running") => event(sequence, "user_input", status);
const reply = (sequence: number) => event(sequence, "agent_output", "running");
const call = (sequence: number, id: string, tool = "Bash") =>
  event(sequence, "tool_call", "running", { item_id: id, tool_name: tool, payload: { input: { command: "sleep 9" } } });
const result = (sequence: number, id: string, extra: Record<string, unknown> = {}) =>
  event(sequence, "tool_result", "running", { item_id: id, ...extra });
const note = (sequence: number, status: string, extra: Record<string, unknown> = {}) =>
  event(sequence, "system_note", status, extra);

const startOf = (events: EventRecord[]) => turnStart(events).startedAt;

describe("turnStart", () => {
  it("times a turn from its message", () => {
    const events = [message(1), call(2, "a"), result(3, "a"), note(4, "idle"), message(5), reply(6)];
    expect(turnStart(events)).toEqual({ startedAt: at(5), bounded: true });
  });

  it("keeps the start when a message steers a running turn", () => {
    expect(startOf([note(1, "idle"), message(2), call(3, "a"), message(4), reply(5)])).toBe(at(2));
  });

  it("times a turn with no message from its first activity", () => {
    const events = [message(1), note(2, "idle"), ev(3, "system_note", "task done"), call(4, "a"), reply(5)];
    expect(startOf(events)).toBe(at(4));
  });

  it("takes the first activity over a message that follows it", () => {
    expect(startOf([note(1, "idle"), call(2, "a"), message(3), reply(4)])).toBe(at(2));
  });

  it("leaves out notes recorded as running ahead of the message", () => {
    const events = [
      note(1, "idle"),
      note(2, "running", { method: "system.commands_changed" }),
      event(3, "status_update", "running"),
      message(4),
      reply(5),
    ];
    expect(startOf(events)).toBe(at(4));
  });

  it.each(["starting", "waiting_input"])("counts %s activity", (status) => {
    expect(startOf([note(1, "idle"), event(2, "approval_request", status), reply(3)])).toBe(at(2));
  });

  it("counts a message recorded as idle", () => {
    expect(startOf([note(1, "idle"), message(2, "idle"), note(3, "running")])).toBe(at(2));
  });

  it("ends a turn that leaves a question open", () => {
    const events = [message(1), note(2, "waiting_input", { awaiting_answer: true }), message(3), reply(4)];
    expect(startOf(events)).toBe(at(3));
  });

  it("stays with a turn that waits on an approval", () => {
    expect(startOf([note(1, "idle"), message(2), event(3, "approval_request", "waiting_input"), reply(4)])).toBe(at(2));
  });

  it.each(["interrupted", "error", "exited"])("starts anew at the message after events that trail an %s end", (status) => {
    const events = [message(1), note(2, status), result(3, "a"), reply(4), message(5), reply(6)];
    expect(startOf(events)).toBe(at(5));
  });

  it("runs through a compaction in the middle of a turn", () => {
    const events = [
      note(1, "idle"),
      message(2),
      call(3, "a"),
      note(4, "running", { method: "system.status" }),
      note(5, "idle", { method: "system.status" }),
      note(6, "idle", { method: "system.compact_boundary" }),
      reply(7),
    ];
    expect(startOf(events)).toBe(at(2));
  });

  it("starts anew at the message after a compaction", () => {
    const events = [message(1), note(2, "idle", { method: "system.compact_boundary" }), message(3), reply(4)];
    expect(startOf(events)).toBe(at(3));
  });

  it("ends a turn at a manual compaction's result note", () => {
    const events = [message(1), note(2, "idle", { method: "result", stop_reason: "compact" }), reply(3)];
    expect(startOf(events)).toBe(at(3));
  });

  it("is unbounded while no earlier end is loaded", () => {
    expect(turnStart([call(1, "a"), result(2, "a")])).toEqual({ startedAt: at(1), bounded: false });
    expect(turnStart([])).toEqual({ startedAt: null, bounded: false });
  });

  it("knows no start once the latest turn has ended", () => {
    expect(turnStart([message(1), note(2, "idle")])).toEqual({ startedAt: null, bounded: true });
  });
});

describe("findTurnStart", () => {
  const page = (events: EventRecord[], has_more = true) => ({ events, has_more });

  it("pages back to the end of the turn before", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(page([call(5, "b"), result(6, "b")]))
      .mockResolvedValueOnce(page([note(2, "idle"), message(3), reply(4)]));
    expect(await findTurnStart([reply(7)], 7, fetch)).toBe(at(3));
    expect(fetch.mock.calls).toEqual([[7], [5]]);
  });

  it("keeps the start among the events already held", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(page([note(4, "idle")]));
    expect(await findTurnStart([message(5), reply(6)], 5, fetch)).toBe(at(5));
  });

  it("keeps a start found part way back", async () => {
    const fetch = vi
      .fn()
      .mockResolvedValueOnce(page([message(5), call(6, "a")]))
      .mockResolvedValueOnce(page([note(2, "idle")]));
    expect(await findTurnStart([reply(9)], 9, fetch)).toBe(at(5));
    expect(fetch.mock.calls).toEqual([[9], [5]]);
  });

  it("gives up on a page that moves the cursor nowhere", async () => {
    const fetch = vi.fn().mockResolvedValue(page([]));
    expect(await findTurnStart([reply(3)], 3, fetch)).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("takes the first activity once the history is exhausted", async () => {
    const fetch = vi.fn().mockResolvedValueOnce(page([message(1), reply(2)], false));
    expect(await findTurnStart([reply(3)], 3, fetch)).toBe(at(1));
  });

  it("gives up past its page limit", async () => {
    const fetch = vi.fn().mockImplementation((before: number) => Promise.resolve(page([reply(before - 1)])));
    expect(await findTurnStart([reply(40)], 40, fetch)).toBeNull();
    expect(fetch).toHaveBeenCalledTimes(25);
  });
});

describe("formatElapsed", () => {
  it("counts seconds, then minutes, then hours", () => {
    expect(formatElapsed(-4000)).toBe("0s");
    expect(formatElapsed(42_900)).toBe("42s");
    expect(formatElapsed(60_000)).toBe("1m 0s");
    expect(formatElapsed(3_600_000)).toBe("1h 0m");
    expect(formatElapsed(72_000)).toBe("1m 12s");
    expect(formatElapsed(120_000)).toBe("2m 0s");
    expect(formatElapsed(3_900_000)).toBe("1h 5m");
  });
});

describe("workingStep", () => {
  const step = (events: EventRecord[]) => workingStep(deriveTranscript(events, "summary").turns, events);

  it("says nothing while a run line shows the running call", () => {
    expect(step([message(1), call(2, "a")])).toBeNull();
  });

  it("names a running call that no run line shows as live", () => {
    const events = [message(1), call(2, "a"), reply(3), call(4, "b", "Read"), result(5, "b")];
    expect(step(events)).toBe("Running bash");
  });

  it("leaves a running call to the live line even with a reply after it", () => {
    expect(step([message(1), call(2, "a"), reply(3)])).toBeNull();
  });

  it("names a command that has only streamed output", () => {
    const delta = result(3, "a", { method: "item/commandExecution/outputDelta" });
    expect(step([message(1), call(2, "a"), delta])).toBe("Running bash");
  });

  it("is thinking after a message, a finished call or reasoning", () => {
    expect(step([message(1)])).toBe("Thinking");
    expect(step([message(1), call(2, "a"), result(3, "a")])).toBe("Thinking");
    expect(step([message(1), event(2, "agent_output", "running", { item_kind: "reasoning" })])).toBe("Thinking");
    expect(step([message(1), note(2, "idle")])).toBe("Thinking");
  });

  it("says nothing while a reply is being written", () => {
    expect(step([message(1), reply(2)])).toBeNull();
    expect(step([message(1), reply(2), note(3, "running")])).toBeNull();
  });

  it("is thinking after a result that follows a reply, or a blank reply", () => {
    expect(step([message(1), call(2, "a"), reply(3), result(4, "a")])).toBe("Thinking");
    expect(step([message(1), event(2, "agent_output", "running", {}, " ")])).toBe("Thinking");
  });

  it("names the latest of the running calls, as its badge reads", () => {
    const events = [message(1), call(2, "a"), call(3, "b", "mcp__files__search"), reply(4), call(5, "c", "Read"), result(6, "c")];
    expect(step(events)).toBe("Running files·search");
  });

  it("ignores calls that never record a result", () => {
    expect(step([message(1), call(2, "f", "SendUserFile")])).toBe("Thinking");
    expect(step([message(1), call(2, "f", "SendUserFile"), reply(3)])).toBeNull();
  });

  it("leaves a cut-short turn's calls behind", () => {
    expect(step([message(1), call(2, "a"), note(3, "interrupted"), message(4)])).toBe("Thinking");
    expect(step([message(1), note(2, "interrupted"), call(3, "a"), message(4)])).toBe("Thinking");
  });
});
