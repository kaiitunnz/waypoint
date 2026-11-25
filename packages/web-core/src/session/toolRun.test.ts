import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import { buildTranscriptItems, type TranscriptItem } from "./transcript";
import {
  describeCall,
  formatCallDuration,
  summarizeToolRun,
  toolNamesOfRun,
  toolRunCounts,
} from "./toolRun";

type Child = Extract<TranscriptItem, { kind: "single" | "pair" }>;

const call = (seq: number, id: string, tool: string, input: Record<string, unknown> = {}) =>
  ev(seq, "tool_call", "", { item_id: id, tool_name: tool, payload: { input } });
const result = (seq: number, id: string, text = "", extra: Record<string, unknown> = {}) =>
  ev(seq, "tool_result", text, { item_id: id, ...extra });

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
      { glyph: "▤", count: 5 },
      { glyph: "›_", count: 1 },
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
    expect(summary.glyphs).toEqual([{ glyph: "◇", count: 1 }]);
  });

  it("names a run of follow-ups alone", () => {
    const summary = summarizeToolRun(run([call(1, "a", "Wait"), call(2, "b", "Wait")]));
    expect(summary.phrase).toBe("Followed up on 2 subagents");
    expect(summary.glyphs).toEqual([{ glyph: "◇", count: 2 }]);
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
    expect(describeCall(item)).toMatchObject({ label: "npm test", mono: true, output: "ok 3 tests" });
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
      output: "collecting\n12 passed in 1.84s",
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
    expect(describeCall(item)).toMatchObject({ label: "set -e\n  npm run lint &&\n    npm test", output: null });
  });

  it("keeps the indentation of a command's first output line", () => {
    const [item] = run([call(1, "a", "Bash", { command: "git status -s" }), ev(2, "tool_result", "\n M a.ts\n?? b.ts\n\n", { item_id: "a" })]);
    expect(describeCall(item)?.output).toBe(" M a.ts\n?? b.ts");
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

describe("formatCallDuration", () => {
  it("scales from tenths to minutes", () => {
    expect(formatCallDuration(1840)).toBe("1.8s");
    expect(formatCallDuration(42_000)).toBe("42s");
    expect(formatCallDuration(124_000)).toBe("2m 4s");
    expect(formatCallDuration(120_000)).toBe("2m");
  });
});
