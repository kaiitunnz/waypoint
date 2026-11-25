import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ActivityLoader, deriveActivity, describeStep } from "./activity";
import type { EventRecord } from "./types";
import { ev } from "./test-helpers";

const claudeTool = (sequence: number, name: string, input: Record<string, unknown>) =>
  ev(sequence, "tool_call", name, { tool_name: name, payload: { type: "tool_use", name, input } });

const agent = (sequence: number, text: string) => ev(sequence, "agent_output", text);

const stepOf = (event: EventRecord) => {
  const step = describeStep(event);
  return step && [step.verb, step.subject].filter(Boolean).join(" ");
};

describe("describeStep", () => {
  it("names shell commands, edits and reads in plain words", () => {
    expect(stepOf(claudeTool(1, "Bash", { command: "npm run lint\nmore" }))).toBe("Running npm run lint");
    expect(stepOf(claudeTool(2, "Edit", { file_path: "/a/backend/runtime.py" }))).toBe("Editing runtime.py");
    expect(stepOf(claudeTool(3, "Read", { file_path: "/a/b.ts" }))).toBe("Reading b.ts");
    expect(stepOf(claudeTool(4, "Grep", { pattern: "x" }))).toBe("Searching the code");
    expect(stepOf(claudeTool(5, "Agent", {}))).toBe("Running a subagent");
  });

  it("separates the verb from the command or file it acts on", () => {
    expect(describeStep(claudeTool(1, "Bash", { command: "npm test" }))).toEqual({ verb: "Running", subject: "npm test" });
    expect(describeStep(claudeTool(2, "Edit", { file_path: "/a/b.py" }))).toEqual({ verb: "Editing", subject: "b.py" });
    expect(describeStep(claudeTool(3, "Grep", { pattern: "x" }))).toEqual({ verb: "Searching the code", subject: null });
  });

  it("unwraps a Codex shell command and truncates a long one", () => {
    const codex = ev(1, "tool_call", "", {
      item: { item: { command: '/bin/bash -lc "npm run lint -- --fix"' } },
    });
    expect(stepOf(codex)).toBe("Running npm run lint -- --fix");
    const long = stepOf(claudeTool(2, "Bash", { command: "x".repeat(200) })) ?? "";
    expect(long.length).toBeLessThanOrEqual("Running ".length + 80);
    expect(long.endsWith("…")).toBe(true);
  });

  it("falls back to the tool name, or nothing", () => {
    expect(stepOf(claudeTool(1, "mcp__thing", {}))).toBe("mcp__thing");
    expect(stepOf(ev(1, "tool_call", ""))).toBeNull();
  });
});

describe("deriveActivity", () => {
  it("carries the step only while it is the line", () => {
    const events = [agent(1, "Looking at it."), claudeTool(2, "Bash", { command: "npm test" })];
    expect(deriveActivity(events, null, "running").step).toEqual({ verb: "Running", subject: "npm test" });
    expect(deriveActivity(events, null, "idle").step).toBeNull();
  });

  it("shows the current step while working", () => {
    const events = [agent(1, "Looking at it."), claudeTool(2, "Bash", { command: "npm test" })];
    expect(deriveActivity(events, null, "running").line).toBe("Running npm test");
  });

  it("falls back to the last message while working with no step", () => {
    expect(deriveActivity([agent(1, "Thinking about it")], null, "running").line).toBe("Thinking about it");
  });

  it("shows the last reply, joined from streamed deltas, when idle", () => {
    const events: EventRecord[] = [
      agent(1, "Old reply."),
      claudeTool(2, "Bash", { command: "ls" }),
      agent(3, "**Sweep"),
      agent(4, " finished:"),
      agent(5, " 48 configs.\n\nMore detail below."),
      ev(6, "system_note", "Turn completed"),
    ];
    expect(deriveActivity(events, null, "idle").line).toBe("Sweep finished: 48 configs.");
  });

  it("cleans markdown and caps the line", () => {
    expect(deriveActivity([agent(1, "## `Done` - all green")], null, "idle").line).toBe("Done - all green");
    expect(deriveActivity([agent(1, "Sent the [T4 readout](/home/x/report.md) and ![chart](a.png)")], null, "idle").line).toBe(
      "Sent the T4 readout and chart",
    );
    expect(deriveActivity([agent(1, "See [the run](https://x.test/a_(b)) now")], null, "idle").line).toBe("See the run now");
    const long = deriveActivity([agent(1, "word ".repeat(80))], null, "idle").line ?? "";
    expect(long.length).toBeLessThanOrEqual(140);
    expect(long.endsWith("…")).toBe(true);
  });

  it("has no line without events", () => {
    expect(deriveActivity([], null, "idle")).toEqual({ line: null, step: null, progress: null });
  });

  it("reports task progress until every task is done", () => {
    const todo = (statuses: string[]) =>
      ev(9, "tool_call", "TodoWrite", {
        tool_name: "TodoWrite",
        tool_input: { todos: statuses.map((status, i) => ({ content: `t${i}`, status })) },
      });
    expect(deriveActivity([], todo(["completed", "in_progress", "pending"]), "running").progress).toEqual({
      done: 1,
      total: 3,
    });
    expect(deriveActivity([], todo(["completed", "completed"]), "idle").progress).toBeNull();
  });
});

describe("ActivityLoader", () => {
  beforeEach(() => vi.useFakeTimers());
  afterEach(() => vi.useRealTimers());

  const make = (overrides: { settleMs?: number; maxWaitMs?: number; concurrency?: number } = {}) => {
    const resolvers: Array<() => void> = [];
    const fetch = vi.fn(
      (id: string) =>
        new Promise<string>((resolve) => {
          resolvers.push(() => resolve(`result:${id}`));
        }),
    );
    const onResult = vi.fn();
    const loader = new ActivityLoader<string>({ fetch, onResult, ...overrides });
    const settle = async () => {
      resolvers.splice(0).forEach((resolve) => resolve());
      await vi.advanceTimersByTimeAsync(0);
    };
    return { loader, fetch, onResult, settle };
  };

  it("fetches a session the first time it is seen, at once", async () => {
    const { loader, fetch, onResult, settle } = make();
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    await settle();
    expect(onResult).toHaveBeenCalledWith("a", "result:a", "t1");
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("does not fetch a session whose result it was given for the same event time", async () => {
    const { loader, fetch } = make();
    loader.seed("a", "t1");
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).not.toHaveBeenCalled();
    loader.request("a", "t2");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("asks again after a failed fetch, once it settles, and then stays quiet", async () => {
    let calls = 0;
    const fetch = vi.fn(async () => {
      calls += 1;
      if (calls === 1) throw new Error("offline");
      return "ok";
    });
    const onResult = vi.fn();
    const loader = new ActivityLoader<string>({ fetch, onResult, settleMs: 1000 });
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(onResult).not.toHaveBeenCalled();

    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(900);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(onResult).toHaveBeenCalledWith("a", "ok", "t1");

    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("fetches a change that settled while an older fetch was still running", async () => {
    const { loader, fetch, settle } = make({ settleMs: 1000 });
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(1);
    loader.request("a", "t2");
    await vi.advanceTimersByTimeAsync(2000);
    expect(fetch).toHaveBeenCalledTimes(1);
    await settle();
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(2);
    expect(fetch).toHaveBeenLastCalledWith("a", expect.anything());
    await settle();
    loader.request("a", "t2");
    await vi.advanceTimersByTimeAsync(10_000);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("keeps a change that arrived during a fetch waiting for its settle", async () => {
    const { loader, fetch, settle } = make({ settleMs: 1000 });
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(0);
    loader.request("a", "t2");
    await settle();
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(600);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("stops asking after repeated failures until the session changes", async () => {
    const fetch = vi.fn(async () => {
      throw new Error("forbidden");
    });
    const loader = new ActivityLoader<string>({ fetch, onResult: vi.fn(), settleMs: 100 });
    for (let attempt = 0; attempt < 6; attempt += 1) {
      loader.request("a", "t1");
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(fetch).toHaveBeenCalledTimes(3);
    loader.request("a", "t2");
    await vi.advanceTimersByTimeAsync(500);
    expect(fetch).toHaveBeenCalledTimes(4);
  });

  it("settles a change before fetching again", async () => {
    const { loader, fetch, settle } = make({ settleMs: 1000 });
    loader.request("a", "t1");
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    loader.request("a", "t2");
    await vi.advanceTimersByTimeAsync(900);
    expect(fetch).toHaveBeenCalledTimes(1);
    loader.request("a", "t3");
    await vi.advanceTimersByTimeAsync(900);
    expect(fetch).toHaveBeenCalledTimes(1);
    await vi.advanceTimersByTimeAsync(200);
    expect(fetch).toHaveBeenCalledTimes(2);
  });

  it("refreshes a constantly changing session within the max wait", async () => {
    const { loader, fetch, settle } = make({ settleMs: 1000, maxWaitMs: 4000 });
    loader.request("a", "t0", Date.now());
    await vi.advanceTimersByTimeAsync(0);
    await settle();
    for (let i = 1; i <= 12; i += 1) {
      loader.request("a", `t${i}`, Date.now());
      await vi.advanceTimersByTimeAsync(500);
    }
    expect(fetch.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("runs at most the concurrency limit at once", async () => {
    const { loader, fetch, settle } = make({ concurrency: 2 });
    ["a", "b", "c"].forEach((id) => loader.request(id, "t1"));
    await vi.advanceTimersByTimeAsync(0);
    expect(fetch).toHaveBeenCalledTimes(2);
    await settle();
    expect(fetch).toHaveBeenCalledTimes(3);
  });

  it("stops everything once disposed", async () => {
    const { loader, fetch, onResult, settle } = make();
    loader.request("a", "t1");
    loader.dispose();
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetch).not.toHaveBeenCalled();
    await settle();
    expect(onResult).not.toHaveBeenCalled();
    loader.request("b", "t1");
    await vi.advanceTimersByTimeAsync(5000);
    expect(fetch).not.toHaveBeenCalled();
  });
});
