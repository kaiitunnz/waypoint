import { describe, expect, it } from "vitest";

import type { EventRecord } from "../types";
import { ev } from "../test-helpers";
import { backgroundTasks } from "./backgroundTasks";

let sequence = 0;
const next = () => (sequence += 1);

const call = (toolUseId: string, tool: string, input: Record<string, unknown>, summary?: string): EventRecord =>
  ev(next(), "tool_call", "", { method: "assistant.tool_use", item_id: toolUseId, tool_use_id: toolUseId, tool_name: tool, payload: { input }, summary });

const result = (toolUseId: string, text: string, extra: Record<string, unknown> = {}): EventRecord =>
  ev(next(), "tool_result", text, { method: "user.tool_result", item_id: toolUseId, tool_use_id: toolUseId, is_error: false, ...extra });

const note = (payload: Record<string, unknown>): EventRecord =>
  ev(next(), "system_note", "", {
    method: "claude.task_notification",
    item_type: "task_notification",
    task_notification: { version: 1, id: `n${sequence}`, ...payload },
  });

const bashLaunch = (toolUseId: string, taskId: string, command = "sleep 600", summary = "Wait for the next cycle") => [
  call(toolUseId, "Bash", { command, run_in_background: true }, summary),
  result(toolUseId, `Command running in background with ID: ${taskId}. Output is being written to: /tmp/${taskId}.output`),
];

const agentLaunch = (toolUseId: string, taskId: string, description = "Survey the settings modal") => [
  call(toolUseId, "Agent", { description, prompt: "..." }),
  result(toolUseId, `Async agent launched successfully.\nagentId: ${taskId} (internal ID)`, { result_deferred: true }),
];

const monitorLaunch = (toolUseId: string, taskId: string) => [
  call(toolUseId, "Monitor", { command: "tail -f log", description: "PR checks" }),
  result(toolUseId, `Monitor started (task ${taskId}, expires in 30m unless the source ends first).`),
];

describe("backgroundTasks", () => {
  it("has none for a session that ran only foreground calls", () => {
    sequence = 0;
    const events = [call("t1", "Bash", { command: "ls" }), result("t1", "a b c"), call("t2", "Agent", { description: "x" }), result("t2", "report")];
    expect(backgroundTasks(events)).toEqual({ running: [], finished: [] });
  });

  it("lists a background command, a subagent and a monitor as running from their launch", () => {
    sequence = 0;
    const events = [...bashLaunch("b1", "bcmd1"), ...agentLaunch("a1", "agent1"), ...monitorLaunch("m1", "mon1")];
    const { running, finished } = backgroundTasks(events);
    expect(finished).toEqual([]);
    expect(running.map((task) => [task.id, task.kind, task.label])).toEqual([
      ["b1", "background_command", "Wait for the next cycle"],
      ["a1", "agent", "Survey the settings modal"],
      ["m1", "monitor", "PR checks"],
    ]);
  });

  it("labels a launch without a description by its command", () => {
    sequence = 0;
    const events = [call("b1", "Bash", { command: "npm run dev\n--port 3000", run_in_background: true }), result("b1", "Command running in background with ID: x1.")];
    expect(backgroundTasks(events).running[0].label).toBe("npm run dev");
  });

  it("finishes a task when its notification names the launching call", () => {
    sequence = 0;
    const events = [
      ...agentLaunch("a1", "agent1"),
      note({ task_id: "agent1", tool_use_id: "a1", kind: "agent", status: "completed", summary: "Agent finished", usage: { tool_uses: 18, duration_ms: 120000 } }),
    ];
    const { running, finished } = backgroundTasks(events);
    expect(running).toEqual([]);
    expect(finished).toHaveLength(1);
    expect(finished[0]).toMatchObject({ id: "a1", status: "completed", usage: ["18 tool uses", "2m"] });
    expect(finished[0].endedAt).not.toBeNull();
  });

  it("finishes a task whose notification names only the task, through the launch's own id", () => {
    sequence = 0;
    const events = [...bashLaunch("b1", "bcmd1"), note({ task_id: "bcmd1", kind: "background_command", status: "failed", summary: "Background command failed" })];
    expect(backgroundTasks(events).finished.map((task) => [task.id, task.status])).toEqual([["b1", "failed"]]);
  });

  it("keeps a monitor running across its events and ends it on its terminal report", () => {
    sequence = 0;
    const launch = monitorLaunch("m1", "mon1");
    const event = note({ task_id: "mon1", kind: "monitor", summary: 'Monitor event: "e2e"', event: "PASS" });
    let { running } = backgroundTasks([...launch, event]);
    expect(running).toHaveLength(1);
    expect(running[0].lastEvent).toBe("PASS");
    const ended = backgroundTasks([...launch, event, note({ task_id: "mon1", tool_use_id: "m1", kind: "monitor", status: "completed", summary: "Monitor stream ended" })]);
    running = ended.running;
    expect(running).toEqual([]);
    expect(ended.finished[0].status).toBe("completed");
  });

  it("ends every earlier task of its kind on the sweep that follows a resume", () => {
    sequence = 0;
    const events = [
      ...bashLaunch("b1", "bcmd1"),
      ...bashLaunch("b2", "bcmd2"),
      ...agentLaunch("a1", "agent1"),
      note({ task_id: "bcmd1", kind: "background_command", status: "stopped", summary: "2 background shell command tasks didn't finish before the previous session ended" }),
      ...bashLaunch("b3", "bcmd3"),
    ];
    const { running, finished } = backgroundTasks(events);
    expect(running.map((task) => task.id)).toEqual(["a1", "b3"]);
    expect(finished.map((task) => [task.id, task.status]).sort()).toEqual([
      ["b1", "stopped"],
      ["b2", "stopped"],
    ]);
  });

  it("ignores a report that matches no launch in the loaded events", () => {
    sequence = 0;
    const events = [...bashLaunch("b1", "bcmd1"), note({ task_id: "older", tool_use_id: "gone", kind: "background_command", status: "completed", summary: "done" })];
    expect(backgroundTasks(events).running.map((task) => task.id)).toEqual(["b1"]);
  });

  it("has nothing running once the session has exited", () => {
    sequence = 0;
    const events = [...bashLaunch("b1", "bcmd1"), ...agentLaunch("a1", "agent1"), note({ task_id: "agent1", tool_use_id: "a1", kind: "agent", status: "completed", summary: "done" })];
    const { running, finished } = backgroundTasks(events, { exited: true });
    expect(running).toEqual([]);
    expect(finished.map((task) => task.id)).toEqual(["a1"]);
  });

  it("orders finished tasks newest end first", () => {
    sequence = 0;
    const events = [
      ...bashLaunch("b1", "bcmd1"),
      ...bashLaunch("b2", "bcmd2"),
      note({ task_id: "bcmd2", tool_use_id: "b2", kind: "background_command", status: "completed", summary: "two" }),
      note({ task_id: "bcmd1", tool_use_id: "b1", kind: "background_command", status: "completed", summary: "one" }),
    ];
    expect(backgroundTasks(events).finished.map((task) => task.id)).toEqual(["b1", "b2"]);
  });

  it("reads events given out of order", () => {
    sequence = 0;
    const events = [...bashLaunch("b1", "bcmd1"), note({ task_id: "bcmd1", tool_use_id: "b1", kind: "background_command", status: "completed", summary: "done" })];
    expect(backgroundTasks([...events].reverse()).finished.map((task) => task.id)).toEqual(["b1"]);
  });
});
