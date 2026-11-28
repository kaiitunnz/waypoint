import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { TASK_NOTIFICATION_FAMILIES } from "./taskNotificationView";
import { buildTranscriptItems, type TranscriptItem } from "./transcript";
import {
  describeCall,
  liveCall,
  pendingCallOf,
  formatCallDuration,
  summarizeToolRun,
  toolFamilyOf,
  toolNamesOfRun,
  toolRunCounts,
} from "./toolRun";

type Child = Extract<TranscriptItem, { kind: "single" | "pair" }>;

const call = (seq: number, id: string, tool: string, input: Record<string, unknown> = {}) =>
  ev(seq, "tool_call", "", { item_id: id, tool_name: tool, payload: { input } });
const result = (seq: number, id: string, text = "", extra: Record<string, unknown> = {}) =>
  ev(seq, "tool_result", text, { item_id: id, ...extra });
const output = (text: string, cut = false) => ({ kind: "output", text, cut });
const report = (text: string, cut = false) => ({ kind: "report", text, cut });

function run(events: ReturnType<typeof ev>[]): Child[] {
  const [item] = buildTranscriptItems(events);
  if (item.kind !== "tool_run") throw new Error(`expected a tool run, got ${item.kind}`);
  return item.items;
}

const diffMeta = (additions: number, deletions: number, path = "/a/b.py") => ({
  diff_preview: {
    files: [{ path, change_type: "update", diff: "", additions, deletions }],
    total_additions: additions,
    total_deletions: deletions,
    phase: "applied",
  },
});

describe("toolRunCounts", () => {
  it("buckets tool names", () => {
    expect(
      toolRunCounts(["Bash", "Edit", "Write", "Read", "Grep", "Glob", "TodoWrite", "Task", "Skill", "Monitor", "Foo"]),
    ).toEqual({ bash: 1, edit: 2, read: 3, todo: 1, agent: 1, skill: 1, monitor: 1, other: 1 });
  });
});

describe("toolNamesOfRun", () => {
  it("names each call, tagging todo lists as TodoWrite", () => {
    const items = run([
      call(1, "a", "Read"),
      result(2, "a"),
      ev(3, "tool_result", "", { item_type: "todo_list", item_id: "t" }),
    ]);
    expect(toolNamesOfRun(items)).toEqual(["Read", "TodoWrite"]);
  });
});

describe("summarizeToolRun", () => {
  it("reads like a sentence in a fixed order", () => {
    const items = run([
      call(1, "a", "Bash", { command: "ls" }),
      result(2, "a"),
      call(3, "b", "Read", { file_path: "/x" }),
      result(4, "b"),
      call(5, "c", "Read", { file_path: "/y" }),
      call(6, "d", "Read", { file_path: "/z" }),
      call(7, "e", "Grep", { pattern: "p" }),
      call(8, "f", "Grep", { pattern: "q" }),
    ]);
    const summary = summarizeToolRun(items);
    expect(summary.phrase).toBe("Read 3 files, searched 2 patterns, ran 1 command");
    expect(summary.glyphs).toEqual([
      { glyph: "▤", label: "Reads and searches", count: 5, family: "read" },
      { glyph: "›_", label: "Commands", count: 1, family: "bash" },
    ]);
  });

  it("counts edited files once and sums the changes", () => {
    const items = run([
      call(1, "a", "Edit", { file_path: "/a/b.py" }),
      result(2, "a", "", diffMeta(10, 2)),
      call(3, "b", "Edit", { file_path: "/a/b.py" }),
      result(4, "b", "", diffMeta(4, 1)),
      call(5, "c", "Bash", { command: "pytest" }),
    ]);
    const summary = summarizeToolRun(items);
    expect(summary.phrase).toBe("Edited 1 file, ran 1 command");
    expect(summary.additions).toBe(14);
    expect(summary.deletions).toBe(3);
  });

  it("names the files of an edit from its diff when the input carries no path", () => {
    const files = ["a.py", "b.py", "c.py"].map((path) => ({ path, change_type: "update", diff: "", additions: 1, deletions: 0 }));
    const multi = { diff_preview: { files, total_additions: 3, total_deletions: 0, phase: "applied" } };
    const items = run([call(1, "a", "Edit"), result(2, "a", "", multi), call(3, "b", "Edit"), result(4, "b", "", diffMeta(1, 0, "a.py"))]);
    expect(summarizeToolRun(items).phrase).toBe("Edited 3 files");
    expect(summarizeToolRun(run([call(1, "a", "Edit"), result(2, "a")])).phrase).toBe("Edited 1 file");
  });

  it("uses singular and plural forms", () => {
    expect(summarizeToolRun(run([call(1, "a", "Grep", { pattern: "p" })])).phrase).toBe("Searched 1 pattern");
    expect(summarizeToolRun(run([call(1, "a", "Skill"), call(2, "b", "Skill")])).phrase).toBe("Used 2 skills");
    expect(summarizeToolRun(run([call(1, "a", "WebSearch")])).phrase).toBe("Searched the web");
    expect(summarizeToolRun(run([call(1, "a", "Mystery")])).phrase).toBe("Used 1 other tool");
  });

  it("counts one subagent once however many calls follow it", () => {
    const items = run([
      call(1, "a", "spawnAgent"),
      call(2, "b", "Wait"),
      call(3, "c", "closeAgent"),
    ]);
    const summary = summarizeToolRun(items);
    expect(summary.phrase).toBe("Ran 1 subagent");
    expect(summary.glyphs).toEqual([{ glyph: "◇", label: "Subagents", count: 1, family: "agent" }]);
  });

  it("names a run of follow-ups alone", () => {
    const summary = summarizeToolRun(run([call(1, "a", "Wait"), call(2, "b", "Wait")]));
    expect(summary.phrase).toBe("Followed up on 2 subagents");
    expect(summary.glyphs).toEqual([{ glyph: "◇", label: "Subagents", count: 2, family: "agent" }]);
  });

  it("counts a todo list with no tool name", () => {
    const items = run([ev(1, "tool_result", "", { item_type: "todo_list", item_id: "t" })]);
    expect(summarizeToolRun(items).phrase).toBe("Updated the task list");
  });
});

describe("describeCall", () => {
  it("describes an edit with its path and changes", () => {
    const [item] = run([call(1, "a", "Edit", { file_path: "/a/b.py" }), result(2, "a", "", diffMeta(14, 3))]);
    expect(describeCall(item)).toMatchObject({
      glyph: "✎",
      toolLabel: "Edit",
      path: { dir: "/a/", base: "b.py" },
      mono: true,
      additions: 14,
      deletions: 3,
      status: "done",
    });
  });

  it("shows a path inside the working directory relative to it and keeps the full one", () => {
    const [inside] = run([call(1, "a", "Read", { file_path: "/repo/backend/src/runtime.py" })]);
    expect(describeCall(inside, "/repo")).toMatchObject({
      label: "backend/src/runtime.py",
      path: { dir: "backend/src/", base: "runtime.py" },
      title: "/repo/backend/src/runtime.py",
    });
    const [outside] = run([call(1, "a", "Edit", { file_path: "/etc/hosts" })]);
    expect(describeCall(outside, "/repo")).toMatchObject({ path: { dir: "/etc/", base: "hosts" }, title: "/etc/hosts" });
    expect(describeCall(inside)?.path).toEqual({ dir: "/repo/backend/src/", base: "runtime.py" });
  });

  it("has no path for a command", () => {
    const [bash] = run([call(1, "a", "Bash", { command: "ls /repo" })]);
    expect(describeCall(bash, "/repo")).toMatchObject({ path: null, title: "ls /repo", label: "ls /repo" });
  });

  it("shortens the directory a search looks in and keeps the full row as its title", () => {
    const [grep] = run([call(1, "a", "Grep", { pattern: "needle", path: "/repo/backend" })]);
    expect(describeCall(grep, "/repo")).toMatchObject({
      label: "needle\nbackend",
      title: "needle  ·  /repo/backend",
    });
  });

  it("describes a search by its pattern, not the directory it looks in", () => {
    const [grep] = run([call(1, "a", "Grep", { pattern: "needle", path: "src/" }), result(2, "a", "src/a.py:3:needle")]);
    const row = describeCall(grep);
    expect(row?.path).toBeNull();
    expect(row?.mono).toBe(true);
    expect(row?.label).toBe("needle\nsrc/");
    const [glob] = run([call(1, "a", "Glob", { pattern: "**/*.py", path: "src/" })]);
    expect(describeCall(glob)?.label).toContain("**/*.py");
  });

  it("lists the arguments of a web call and of a tool with no card of its own", () => {
    const [web] = run([call(1, "a", "WebFetch", { url: "https://example.test/a?b=1" })]);
    expect(describeCall(web)).toMatchObject({ label: "https://example.test/a?b=1", mono: true });
    const [other] = run([call(1, "a", "mcp__thing", { id: 7, query: "x y", filters: { a: 1 } })]);
    expect(describeCall(other)).toMatchObject({ label: 'id: 7\nquery: x y\nfilters: {"a":1}', mono: true });
    const [agent] = run([call(1, "a", "Task", { description: "Explore the repo", prompt: "p" })]);
    expect(describeCall(agent)?.mono).toBe(false);
  });

  it("keeps the reason a non-command call failed, and only then", () => {
    const [failed] = run([call(1, "a", "Edit", { file_path: "/repo/a.py" }), result(2, "a", "String not found in file", { is_error: true })]);
    expect(describeCall(failed)).toMatchObject({ status: "failed", reply: output("String not found in file") });
    const [read] = run([call(1, "b", "Read", { file_path: "/repo/a.py" }), result(2, "b", "contents")]);
    expect(describeCall(read)?.reply).toBeNull();
  });

  it("shows no command block for a blank command", () => {
    const [bash] = run([call(1, "a", "Bash", { command: "  \n " })]);
    expect(describeCall(bash)?.mono).toBe(false);
  });

  it("carries the attachments of a call or its result", () => {
    const spec = { id: "img", filename: "a.png", kind: "image", size: 10 };
    const [pair] = run([call(1, "a", "mcp__draw", { prompt: "a cat" }), result(2, "a", "", { attachments: [spec] })]);
    expect(describeCall(pair)?.attachments).toEqual([spec]);
    const [single] = run([ev(1, "tool_call", "", { item_id: "b", tool_name: "mcp__draw", payload: { input: {} }, attachments: [spec] })]);
    expect(describeCall(single)?.attachments).toEqual([spec]);
  });

  it("keeps every argument of a tool whose input also carries a path", () => {
    const [other] = run([call(1, "a", "mcp__fs", { path: "/repo/a.txt", mode: "read" })]);
    expect(describeCall(other, "/repo")).toMatchObject({ path: null, label: "path: /repo/a.txt\nmode: read" });
  });

  it("reads a Codex command from the item and its output without the echoed command line", () => {
    const started = ev(1, "tool_call", "$ /bin/bash -lc 'npm test'", {
      item_id: "a",
      tool_name: "Bash",
      payload: { item: { type: "commandExecution", command: "/bin/bash -lc 'npm test'" } },
    });
    const done = ev(2, "tool_result", "$ /bin/bash -lc 'npm test'\nok 3 tests", {
      item_id: "a",
      tool_name: "Bash",
      payload: { item: { type: "commandExecution", command: "/bin/bash -lc 'npm test'", aggregatedOutput: "ok 3 tests\n" } },
    });
    const [item] = run([started, done]);
    expect(describeCall(item)).toMatchObject({ label: "npm test", mono: true, reply: output("ok 3 tests") });
  });

  it("reads a todo list's entries and its progress", () => {
    const todos = [
      { content: "a", status: "completed", activeForm: "A" },
      { content: "b", status: "pending", activeForm: "B" },
    ];
    const [item] = run([call(1, "t", "TodoWrite", { todos }), result(2, "t", "ok", { payload: { input: { todos } } })]);
    const row = describeCall(item);
    expect(row?.label).toBe("Tasks 1 of 2 done");
    expect(row?.todos?.map((entry) => entry.text)).toEqual(["a", "b"]);
  });

  it("describes a command with its output and duration", () => {
    const [item] = run([
      call(1, "a", "Bash", { command: "uv run pytest -q" }),
      ev(4, "tool_result", "collecting\n12 passed in 1.84s\n", { item_id: "a" }),
    ]);
    const row = describeCall(item);
    expect(row).toMatchObject({
      glyph: "›_",
      label: "uv run pytest -q",
      reply: output("collecting\n12 passed in 1.84s"),
      status: "done",
      path: null,
      mono: true,
    });
    expect(row?.durationMs).toBe(3000);
  });

  it("keeps a multi-line command's line breaks", () => {
    const [item] = run([
      call(1, "a", "Bash", { command: "\n  set -e\n  npm run lint &&\n    npm test\n" }),
      ev(2, "tool_result", "", { item_id: "a" }),
    ]);
    expect(describeCall(item)).toMatchObject({ label: "set -e\n  npm run lint &&\n    npm test", reply: null });
  });

  it("keeps the indentation of a command's first output line", () => {
    const [item] = run([call(1, "a", "Bash", { command: "git status -s" }), ev(2, "tool_result", "\n M a.ts\n?? b.ts\n\n", { item_id: "a" })]);
    expect(describeCall(item)?.reply?.text).toBe(" M a.ts\n?? b.ts");
  });

  it("leaves out a duration too short to be a measurement", () => {
    const [item] = run([
      call(1, "a", "Bash", { command: "ls" }),
      ev(1, "tool_result", "x", { item_id: "a" }, { ts: "2026-01-01T00:00:01.200Z" }),
    ]);
    expect(describeCall(item)?.durationMs).toBeNull();
  });

  it("marks an unanswered call pending and a failed result failed", () => {
    const [pending] = run([call(1, "a", "Bash", { command: "sleep 9" })]);
    expect(describeCall(pending)?.status).toBe("pending");
    const [failed] = run([call(1, "a", "Bash", { command: "false" }), result(2, "a", "boom", { is_error: true })]);
    expect(describeCall(failed)?.status).toBe("failed");
  });

  it("is null for a note absorbed into the run", () => {
    const items = run([call(1, "a", "Read", { file_path: "/x" }), ev(2, "system_note", "attached")]);
    expect(describeCall(items[1])).toBeNull();
  });
});

// Claude as its events record it: the input under the payload, no tool name on the result.
const claudeCall = (tool: string, input: Record<string, unknown>) =>
  ev(1, "tool_call", `${tool}\n${JSON.stringify(input, null, 2)}`, { item_id: "a", tool_name: tool, payload: { input } });

const codexItem = (
  kind: "tool_call" | "tool_result",
  tool: string,
  itemType: string,
  item: Record<string, unknown>,
  text: string,
) =>
  ev(kind === "tool_call" ? 1 : 3, kind, text, {
    item_id: "a",
    tool_name: tool,
    item_type: itemType,
    payload: { item: { type: itemType, ...item } },
  });

const openCode = (kind: "tool_call" | "tool_result", tool: string, input: Record<string, unknown>, text: string) =>
  ev(kind === "tool_call" ? 1 : 3, kind, text, { item_id: "a", tool_name: tool, tool_input: input });

const only = (...events: ReturnType<typeof ev>[]) => describeCall(run(events)[0]);

describe("describeCall tool names", () => {
  it("leads a tool with no card of its own with its name, its arguments below", () => {
    expect(only(claudeCall("mcp__local-notes__lookup_note", { topic: "lamps", limit: 3 }))).toMatchObject({
      name: { server: "local-notes", tool: "lookup_note" },
      label: "topic: lamps\nlimit: 3",
      title: "mcp__local-notes__lookup_note",
      mono: true,
    });
    expect(only(codexItem("tool_call", "docs:search", "mcpToolCall", { arguments: { query: "x" } }, "MCP docs:search"))).toMatchObject({
      name: { server: "docs", tool: "search" },
      label: "query: x",
    });
    expect(only(openCode("tool_call", "docs_search", { query: "x" }, "docs_search({})"))).toMatchObject({
      name: { server: null, tool: "docs_search" },
      label: "query: x",
    });
  });

  it("shows only the name of a call with no arguments", () => {
    expect(only(claudeCall("ListAgents", {}))).toMatchObject({ name: { server: null, tool: "ListAgents" }, label: "" });
    expect(only(openCode("tool_call", "docs_ping", {}, "docs_ping({})"))?.label).toBe("");
    expect(only(codexItem("tool_call", "docs:ping", "mcpToolCall", { arguments: {} }, "MCP docs:ping"))?.label).toBe("");
  });

  it("keeps what a call without structured arguments says, unless it only repeats the name", () => {
    const image = codexItem("tool_call", "ViewImage", "imageView", { path: "/a/shot.png" }, "/a/shot.png");
    expect(only(image)).toMatchObject({ name: { tool: "ViewImage" }, label: "/a/shot.png" });
    expect(only(ev(1, "tool_call", "ping", { item_id: "a", tool_name: "ping" }))?.label).toBe("");
    expect(only(ev(1, "tool_call", "docs:ping", { item_id: "a", tool_name: "docs:ping" }))?.label).toBe("");
  });

  it("names watched tasks and follow-ups, which their arguments do not identify", () => {
    expect(only(claudeCall("Monitor", { command: "tail -f log" }))).toMatchObject({ name: { tool: "Monitor" } });
    expect(only(codexItem("tool_call", "wait", "collabAgentToolCall", {}, "wait"))).toMatchObject({
      name: { tool: "Wait" },
      label: "",
    });
    expect(only(claudeCall("Wait", { agent_id: "x" }))?.label).toBe("agent_id: x");
  });

  it("leaves a call that already says what it is unnamed", () => {
    const rows = [
      claudeCall("Bash", { command: "ls" }),
      claudeCall("Read", { file_path: "/a.py" }),
      claudeCall("Grep", { pattern: "x" }),
      claudeCall("WebFetch", { url: "https://example.test" }),
      claudeCall("Skill", { skill: "review" }),
      claudeCall("Agent", { description: "Explore", prompt: "p" }),
      claudeCall("TodoWrite", { todos: [] }),
    ];
    for (const row of rows) expect(only(row)?.name).toBeNull();
  });

  it("reads a prompt and a kind of agent only from a subagent call", () => {
    for (const tool of ["mcp__crew__hire", "Wait"]) {
      expect(only(claudeCall(tool, { agent: "reviewer", prompt: "p" }))).toMatchObject({ agentType: null, prompt: null });
    }
  });

  it("names a subagent call that carries no description", () => {
    expect(only(codexItem("tool_call", "spawnAgent", "collabAgentToolCall", { prompt: "do it" }, "spawnAgent\n\ndo it"))).toMatchObject({
      name: { server: null, tool: "spawnAgent" },
      label: "",
      prompt: "do it",
    });
  });
});

describe("describeCall subagents", () => {
  const agent = claudeCall("Agent", { description: "List docs", subagent_type: "general-purpose", prompt: "List them.\nThen stop." });
  const answer = (text: string, extra: Record<string, unknown> = {}) => ev(3, "tool_result", text, { item_id: "a", ...extra });
  const notification = (fields: Record<string, unknown>) =>
    ev(9, "system_note", "Agent finished", {
      method: "task_notification",
      task_notification: { version: 1, id: "n", kind: "agent", tool_use_id: "a", ...fields },
    });
  // The pair as `deriveTranscript` leaves it once a notification about the call has arrived.
  const reportedFor = (events: ReturnType<typeof ev>[], notice: ReturnType<typeof ev>) => {
    const [item] = run(events);
    if (item.kind !== "pair") throw new Error("expected a pair");
    return describeCall({ ...item, pair: { ...item.pair, report: notice } });
  };
  const reported = (notice: ReturnType<typeof ev>) => reportedFor([agent, answer("launched", { result_deferred: true })], notice);

  it("shows what a running subagent was asked and what kind it is", () => {
    expect(only(agent)).toMatchObject({
      name: null,
      label: "List docs",
      agentType: "general-purpose",
      prompt: "List them.\nThen stop.",
      reply: null,
      status: "pending",
    });
  });

  it("shows the answer a call returns as prose", () => {
    expect(only(agent, answer("# Done\n\n2 files"))).toMatchObject({ reply: report("# Done\n\n2 files"), status: "done" });
  });

  it("keeps a call whose result only acknowledges the launch running, without the acknowledgement", () => {
    const row = only(agent, answer("Async agent launched", { result_deferred: true }));
    expect(row).toMatchObject({ status: "pending", reply: null, durationMs: null });
    expect(row?.prompt).toContain("List them.");
  });

  it("takes the notification's report as the result of a deferred call", () => {
    expect(reported(notification({ status: "completed", result_preview: "2 files", result_truncated: false }))).toMatchObject({
      status: "done",
      reply: report("2 files"),
      durationMs: 8000,
    });
    expect(reported(notification({ status: "completed", result_preview: "2 fi", result_truncated: true }))?.reply).toEqual(report("2 fi", true));
    expect(reported(notification({ result_preview: "ok" }))?.status).toBe("done");
  });

  it("marks a deferred call failed when its report did not complete", () => {
    for (const status of ["failed", "killed", "stopped", "something new"]) {
      expect(reported(notification({ status }))).toMatchObject({ status: "failed", reply: null });
    }
  });

  it("shows a refused launch as the error it is", () => {
    expect(only(agent, answer("Agent type not found", { is_error: true }))).toMatchObject({
      status: "failed",
      reply: output("Agent type not found"),
    });
  });

  it("takes a subagent's report from its notification though its result is not flagged", () => {
    const events = [agent, answer("Async agent launched")];
    const done = notification({ status: "completed", result_preview: "2 files" });
    expect(reportedFor(events, done)).toMatchObject({ reply: report("2 files"), status: "done", durationMs: 8000 });
    // A notification without a report still stands in for the acknowledgement.
    expect(reportedFor(events, notification({ status: "completed" }))).toMatchObject({ reply: null, status: "done" });
  });

  it("shows a deferred command's report as output, and keeps the output of one that was not deferred", () => {
    const make = claudeCall("Bash", { command: "make" });
    const notice = notification({ kind: "background_command", status: "failed", result_preview: "built" });
    expect(reportedFor([make, answer("started", { result_deferred: true })], notice)).toMatchObject({
      reply: output("built"),
      status: "failed",
    });
    expect(reportedFor([make, answer("started in the background")], notice)).toMatchObject({
      reply: output("started in the background"),
      status: "done",
    });
  });

  it("shows an agent's report on any call its notification names", () => {
    const send = claudeCall("SendMessage", { to: "a1", message: "And four." });
    const sent = answer('{"success":true,"message":"Message sent"}');
    expect(reportedFor([send, sent], notification({ status: "completed", result_preview: "four" }))).toMatchObject({
      name: { tool: "SendMessage" },
      reply: report("four"),
      status: "done",
      durationMs: 8000,
    });
    expect(only(send, sent)?.reply?.kind).toBe("output");
  });

  it("reads a launch whose call is not loaded from the notification, not the acknowledgement", () => {
    const [item] = run([ev(3, "tool_result", "Async agent launched", { item_id: "a", result_deferred: true })]);
    if (item.kind !== "pair") throw new Error("expected a pair");
    const notice = notification({ status: "completed", result_preview: "2 files", summary: 'Agent "List docs" finished' });
    expect(describeCall({ ...item, pair: { ...item.pair, report: notice } })).toMatchObject({
      label: 'Agent "List docs" finished',
      reply: report("2 files"),
    });
    expect(describeCall(item)).toMatchObject({ label: "Background task", reply: null, status: "pending" });
  });

  it("carries the files of a report the notification cut", () => {
    const spec = (id: string) => ({ id, filename: `${id}.md`, kind: "file", size: 5000 });
    const files = { attachments: [spec("spill"), spec("transcript")], inline_attachment_ids: ["spill"] };
    const notice = (kind: string) => {
      const event = notification({ kind, status: "completed", result_preview: "start", result_truncated: true });
      return { ...event, metadata: { ...event.metadata, ...files } };
    };
    // An agent's captured output file is its transcript, not its report.
    expect(reported(notice("agent"))?.attachments.map((file) => file.id)).toEqual(["spill"]);
    const failed = notification({ status: "failed" });
    expect(reported({ ...failed, metadata: { ...failed.metadata, attachments: [spec("transcript")] } })?.attachments).toEqual([]);
    const make = [claudeCall("Bash", { command: "make" }), answer("started", { result_deferred: true })];
    const row = reportedFor(make, notice("background_command"));
    expect(row?.reply).toEqual(output("start", true));
    expect(row?.attachments.map((file) => file.id)).toEqual(["spill", "transcript"]);
  });

  it("leaves a deferred call out of the calls still running", () => {
    expect(pendingCallOf(run([agent, answer("launched", { result_deferred: true })]))).toBeUndefined();
  });

  it("reads OpenCode's task call, whose tool name differs", () => {
    const input = { description: "Count", agent: "general", prompt: "Count to three." };
    const row = only(
      openCode("tool_call", "task", input, "task({})"),
      openCode("tool_result", "task", input, "Result for task:\none two three"),
    );
    expect(row).toMatchObject({ toolLabel: "Task", label: "Count", agentType: "general", prompt: "Count to three." });
    expect(row?.reply).toMatchObject({ kind: "report" });
    expect(row?.reply?.text).toContain("one two three");
  });

  it("reads the reports a Codex wait collected", () => {
    const states = { t1: { status: "completed", message: "one" }, t2: { status: "completed", message: "two" } };
    const item = { agentsStates: states };
    const waited = (tool: string) =>
      only(
        codexItem("tool_call", tool, "collabAgentToolCall", {}, tool),
        codexItem("tool_result", tool, "collabAgentToolCall", item, `${tool}\n\none\n\ntwo`),
      );
    expect(waited("wait")).toMatchObject({ reply: report("one\n\ntwo"), label: "" });
    // Closing repeats the report the wait gave.
    expect(waited("closeAgent")?.reply).toBeNull();
  });

  it("shows a message sent to a Codex subagent as its prompt", () => {
    expect(only(codexItem("tool_call", "sendInput", "collabAgentToolCall", { prompt: "And four." }, "sendInput\n\nAnd four."))).toMatchObject({
      name: { tool: "sendInput" },
      label: "",
      prompt: "And four.",
    });
  });

  it("does not repeat the label of a result-only subagent notice", () => {
    const notice = ev(1, "tool_result", "Spawned scout", { item_id: "a", tool_name: "Subagent", item_type: "subAgentActivity" });
    expect(only(notice)).toMatchObject({ name: null, label: "Spawned scout", reply: null });
  });
});

describe("describeCall output of other tools", () => {
  it("shows what a tool with no card of its own returned", () => {
    const call = claudeCall("mcp__local-notes__ping", {});
    expect(only(call, result(3, "a", "\npong\n"))).toMatchObject({ reply: output("pong"), status: "done" });
  });

  it("reads the text of a Codex plugin tool from its content items", () => {
    const item = { arguments: { q: "x" }, contentItems: [{ type: "inputText", text: "one" }, { type: "inputText", text: "two" }] };
    expect(
      only(
        codexItem("tool_call", "app:find", "dynamicToolCall", item, "app:find"),
        codexItem("tool_result", "app:find", "dynamicToolCall", item, "app:find"),
      )?.reply,
    ).toEqual(output("one\ntwo"));
  });

  it("names the media a result carries instead of printing its bytes", () => {
    const content = [
      { type: "text", text: "Saved the shot." },
      { type: "image", source: { type: "base64", media_type: "image/png", data: "iVBORw0KGgo" } },
    ];
    const text = 'Saved the shot.\n{"type": "image", "source": {"data": "iVBORw0KGgo"}}';
    const shot = claudeCall("mcp__browser__screenshot", {});
    expect(only(shot, result(3, "a", text, { payload: { type: "tool_result", content } }))?.reply).toEqual(output("Saved the shot.\n[image]"));
    const references = [{ type: "tool_reference", tool_name: "x" }];
    const search = claudeCall("ToolSearch", { query: "x" });
    expect(only(search, result(3, "a", "as the agent gave it", { payload: { content: references } }))?.reply).toEqual(
      output("as the agent gave it"),
    );
    const items = { contentItems: [{ type: "inputText", text: "one" }, { type: "inputImage", imageUrl: "data:image/png;base64,AAAA" }] };
    expect(
      only(codexItem("tool_call", "app:find", "dynamicToolCall", {}, "app:find"), codexItem("tool_result", "app:find", "dynamicToolCall", items, "app:find"))
        ?.reply,
    ).toEqual(output("one\n[inputImage]"));
  });

  it("names the media of a failed result too", () => {
    const content = [{ type: "image", source: { type: "base64", media_type: "image/png", data: "iVBORw0KGgo" } }];
    const shot = claudeCall("mcp__browser__screenshot", {});
    expect(only(shot, result(3, "a", "iVBORw0KGgo", { is_error: true, payload: { content } }))).toMatchObject({
      status: "failed",
      reply: output("[image]"),
    });
  });

  it("shows nothing more for a result whose call is not loaded and names no tool", () => {
    expect(only(result(3, "a", "line one\nline two"))).toMatchObject({ name: null, reply: null });
  });

  it("does not repeat a result that stands alone in its label", () => {
    const alone = (tool: string, text: string) => only(ev(1, "tool_result", text, { item_id: "a", tool_name: tool }));
    expect(alone("lookup", "line one\nline two")).toMatchObject({ label: "", reply: output("line one\nline two") });
    expect(alone("app:find", "app:find")).toMatchObject({ label: "", reply: null });
  });

  it("leaves out a result that only repeats the call", () => {
    const text = "MCP docs:ping";
    const call = codexItem("tool_call", "docs:ping", "mcpToolCall", {}, text);
    expect(only(call, codexItem("tool_result", "docs:ping", "mcpToolCall", {}, text))?.reply).toBeNull();
  });

  it("adds no output to reads, searches, web calls and skills", () => {
    const calls = [
      claudeCall("Read", { file_path: "/a.py" }),
      claudeCall("Grep", { pattern: "x" }),
      claudeCall("WebFetch", { url: "https://example.test", prompt: "Summarise it." }),
      claudeCall("Skill", { skill: "review" }),
      claudeCall("Edit", { file_path: "/a.py" }),
    ];
    for (const call of calls) expect(only(call, result(3, "a", "contents"))).toMatchObject({ reply: null, prompt: null });
  });
});

describe("formatCallDuration", () => {
  it("scales from tenths to minutes", () => {
    expect(formatCallDuration(1840)).toBe("1.8s");
    expect(formatCallDuration(42_000)).toBe("42s");
    expect(formatCallDuration(124_000)).toBe("2m 4s");
    expect(formatCallDuration(120_000)).toBe("2m");
  });

  it("does not round a near-ten-second call up to 10.0s", () => {
    expect(formatCallDuration(9_949)).toBe("9.9s");
    expect(formatCallDuration(9_999)).toBe("10s");
  });
});

describe("pendingCallOf", () => {
  const run = (...events: ReturnType<typeof ev>[]) =>
    buildTranscriptItems(events).flatMap((item) => (item.kind === "tool_run" ? item.items : []));
  const call = (seq: number, id: string) => ev(seq, "tool_call", "", { item_id: id, tool_name: "Bash" });

  it("takes the latest call without a result", () => {
    const items = run(call(1, "a"), call(2, "b"), call(3, "c"), ev(4, "tool_result", "", { item_id: "c" }));
    expect(pendingCallOf(items)).toBe(items[1]);
    expect(describeCall(items[1])?.status).toBe("pending");
  });

  it("counts a call without an item id, and no stray result", () => {
    const loose = run(ev(1, "tool_call", "", { tool_name: "Bash" }));
    expect(pendingCallOf(loose)).toBe(loose[0]);
    expect(pendingCallOf(run(ev(1, "tool_result", "")))).toBeUndefined();
  });
});

const summarized = (seq: number, id: string, tool: string, input: Record<string, unknown>, summary?: string) =>
  ev(seq, "tool_call", "", { item_id: id, tool_name: tool, payload: { input }, ...(summary ? { summary } : {}) });

describe("a call's summary", () => {
  const bash = { command: "git status --short", description: "Check for changes" };

  it("heads a command that stays whole below it", () => {
    const [item] = run([summarized(1, "a", "Bash", bash, "Check for changes"), result(2, "a", " M a.py")]);
    expect(describeCall(item)).toMatchObject({ summary: "Check for changes", label: "git status --short", mono: true });
  });

  it("is absent when the call carries none", () => {
    const [item] = run([summarized(1, "a", "Bash", bash), result(2, "a", "")]);
    expect(describeCall(item)).toMatchObject({ summary: null, label: "git status --short" });
  });

  it("says a subagent call's label once", () => {
    const input = { description: "Find callers", prompt: "List them", subagent_type: "Explore" };
    const [item] = run([summarized(1, "a", "Agent", input, "Find callers")]);
    expect(describeCall(item)).toMatchObject({ summary: "Find callers", label: "", name: null, agentType: "Explore", prompt: "List them" });
    // A call stored before summaries keeps its label from the input.
    const [stored] = run([summarized(1, "a", "Agent", input)]);
    expect(describeCall(stored)).toMatchObject({ summary: null, label: "Find callers", name: null });
  });

  it("drops the argument that only repeats it, never a command", () => {
    const watch = { command: "Watch the build", description: "Watch the build", persistent: true };
    const [item] = run([summarized(1, "a", "Monitor", watch, "Watch the build")]);
    expect(describeCall(item)?.label).toBe("command: Watch the build\npersistent: true");
    const [bare] = run([summarized(1, "a", "Monitor", watch)]);
    expect(describeCall(bare)?.label).toContain("description: Watch the build");
    // A summary joins the lines of a description into one.
    const [lines] = run([summarized(1, "a", "Monitor", { ...watch, description: "Watch\nthe build" }, "Watch the build")]);
    expect(describeCall(lines)?.label).toBe("command: Watch the build\npersistent: true");
  });

  it("leaves the arguments of a call without one as they are, empty values included", () => {
    const [item] = run([call(1, "a", "mcp__notes__list", { cursor: null, limit: 10 })]);
    expect(describeCall(item)?.label).toBe("cursor: null\nlimit: 10");
  });

  it("heads the live run line of the call in progress, over its command", () => {
    const items = run([
      summarized(1, "a", "Bash", { command: "ls" }, "List the files"),
      result(2, "a", ""),
      summarized(3, "b", "Bash", bash, "Check for changes"),
    ]);
    expect(liveCall(items, true)).toEqual({
      step: { verb: "Running", subject: "git status --short" },
      summary: "Check for changes",
      command: "git status --short",
    });
    expect(liveCall(items, false)).toBeNull();
    expect(liveCall(run([summarized(1, "a", "Bash", { command: "ls" })]), true)).toMatchObject({ summary: null });
  });

  it("keeps a live command to its first line, and says when more follow", () => {
    const command = (text: string) => liveCall(run([summarized(1, "a", "Bash", { command: text })]), true)?.command;
    expect(command("cd src\r\nls -a\n")).toBe("cd src …");
    expect(command("\n  ls -a\n\n")).toBe("ls -a");
    expect(command(`echo ${"x".repeat(200)}`)).toBe(`echo ${"x".repeat(200)}`);
  });

  it("names no command for a live call that runs none", () => {
    const items = run([summarized(1, "a", "Agent", { prompt: "List them" }, "Find callers")]);
    expect(liveCall(items, true)).toEqual({ step: { verb: "Running a subagent", subject: null }, summary: "Find callers", command: null });
  });
});

describe("toolFamilyOf", () => {
  it.each([
    ["Bash", "bash"],
    ["Monitor", "bash"],
    ["Read", "read"],
    ["Grep", "read"],
    ["Glob", "read"],
    ["WebFetch", "read"],
    ["WebSearch", "read"],
    ["Edit", "edit"],
    ["MultiEdit", "edit"],
    ["NotebookEdit", "edit"],
    ["Write", "edit"],
    ["Agent", "agent"],
    ["Task", "agent"],
    ["spawnAgent", "agent"],
    ["Subagent", "agent"],
    ["Wait", "agent"],
    ["closeAgent", "agent"],
    ["TodoWrite", "agent"],
    ["Skill", "skill"],
    ["mcp__notes__ping", null],
    ["notes:ping", null],
    ["ToolSearch", null],
    [null, null],
  ])("puts %s in its family", (tool, family) => {
    expect(toolFamilyOf(tool)).toBe(family);
  });

  it("matches the families of the run line's chips and of notification cards", () => {
    expect(TASK_NOTIFICATION_FAMILIES).toEqual({
      agent: toolFamilyOf("Agent"),
      monitor: toolFamilyOf("Monitor"),
      background_command: toolFamilyOf("Bash"),
      message: null,
      unknown: null,
    });
    const items = run([call(1, "a", "WebSearch", { query: "x" }), call(2, "b", "Bash", { command: "ls" })]);
    expect(summarizeToolRun(items).glyphs.map((entry) => [entry.glyph, entry.family])).toEqual([
      ["›_", "bash"],
      ["ƒ", null],
    ]);
    const [web] = run([call(1, "a", "WebSearch", { query: "x" })]);
    expect(describeCall(web)?.family).toBe("read");
  });
});
