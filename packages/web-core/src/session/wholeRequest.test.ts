import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import type { EventRecord } from "../types";
import { shownRequest, type ShownRequest } from "./wholeRequest";

const ownInput = { cwd: "/w", fromScreen: false };
const screen = { cwd: "/w", fromScreen: true };
const spoke = ev(1, "user_input", "go");

const approve = (tool: string, input: Record<string, unknown> | null, extra: Record<string, unknown> = {}) =>
  ev(9, "approval_request", `Approve ${tool}`, {
    tool_name: tool,
    ...(input ? { tool_input: input } : {}),
    approval_id: "a1",
    ...extra,
  });
const call = (seq: number, id: string | null, tool: string, input: Record<string, unknown>) =>
  ev(seq, "tool_call", tool, { tool_name: tool, ...(id ? { tool_use_id: id } : {}), payload: { input } });
const done = (seq: number, id: string) => ev(seq, "tool_result", "done", { tool_use_id: id });

const onScreen = (request: EventRecord, events: EventRecord[]) => shownRequest(request, [spoke, ...events, request], screen);
const asAsked = (request: EventRecord) => shownRequest(request, [request], ownInput);

describe("a shell command read off the screen", () => {
  // The pane wraps rows into lines of their own and pads them.
  const pane = approve("Bash", { command: "echo a long line that      \nwraps" });
  const real = { command: "echo a long line that wraps", description: "Echo", dangerouslyDisableSandbox: true };

  it("is the transcript's call, sandbox state and all", () => {
    const shown = onScreen(pane, [call(2, "t1", "Bash", real)]);
    expect(shown).toMatchObject({ whole: true, input: real, body: { kind: "command", command: real.command } });
    expect(shown.fields).toMatchObject({ tool_input: real });
    expect(shown.warnings).toEqual(["Runs outside the sandbox"]);
  });

  it("is found when recorded after the request", () => {
    expect(onScreen(pane, [call(10, "t1", "Bash", real)]).input).toEqual(real);
  });

  it("takes several calls that carry one input", () => {
    expect(onScreen(pane, [call(2, "t1", "Bash", real), call(3, "t2", "Bash", { ...real })]).whole).toBe(true);
  });

  it("is locked without a certain call", () => {
    const cases: [string, EventRecord[]][] = [
      ["no call", []],
      ["another command", [call(2, "t1", "Bash", { command: "echo a long line that wraps more" })]],
      ["a command that differs inside a word", [call(2, "t1", "Bash", { command: "echo a long line thatwraps" })]],
      ["answered", [call(2, "t1", "Bash", real), done(3, "t1")]],
      ["calls that differ", [call(2, "t1", "Bash", real), call(3, "t2", "Bash", { command: real.command })]],
      ["a call without an id", [call(2, null, "Bash", real)]],
      ["another tool's call", [call(2, "t1", "mcp__sh__run", real)]],
      ["from before the human spoke", [call(2, "t1", "Bash", real), ev(3, "user_input", "next")]],
    ];
    for (const [name, events] of cases) {
      const shown = shownRequest(pane, [spoke, ...events, pane], screen);
      expect([name, shown.whole, shown.input]).toEqual([name, false, null]);
    }
  });

  it("needs no call where the agent gave the input itself", () => {
    const shown = asAsked(approve("Bash", { command: "ls" }));
    expect(shown).toMatchObject({ whole: true, input: { command: "ls" }, warnings: [] });
  });
});

describe("another tool read off the screen", () => {
  const pane = approve("mcp__mail__send", { description: "Send mail" });
  const input = { to: "a@b.c", body: "hi" };

  it("is the one unanswered call of that tool", () => {
    const events = [call(2, "t0", "mcp__mail__send", { to: "x" }), done(3, "t0"), call(4, "t1", "mcp__mail__send", input)];
    expect(onScreen(pane, events)).toMatchObject({ whole: true, input, fields: { tool_input: input } });
  });

  it("never takes a subagent launch, which a subagent's own dialog reads as", () => {
    for (const tool of ["Agent", "Task"]) {
      const launch = call(2, "t1", tool, { description: "Sweep", prompt: "Delete the caches" });
      expect(onScreen(approve(tool, { description: "Sweep" }), [launch])).toMatchObject({ whole: false, input: null });
    }
  });

  it("is locked on the screen's summary alone", () => {
    for (const events of [[], [call(2, "t1", "mcp__mail__send", input), call(3, "t2", "mcp__mail__send", { to: "x" })]]) {
      expect(onScreen(pane, events)).toMatchObject({ whole: false, input: null });
    }
  });
});

describe("a plan read off the screen", () => {
  it("comes from its plan file", () => {
    expect(onScreen(approve("ExitPlanMode", { plan: "# Do it" }), []).whole).toBe(true);
    expect(onScreen(approve("ExitPlanMode", { plan: "" }), []).whole).toBe(false);
  });
});

describe("a write", () => {
  const pane = approve("Write", { file_path: "sub/a.txt" });
  const write = (seq: number, id: string, path: string, content: string, tool = "Write") =>
    call(seq, id, tool, { file_path: path, content });
  const added = (shown: ShownRequest) =>
    shown.body.kind === "diff" ? [shown.body.preview.files[0].path, shown.body.preview.files[0].diff] : shown.body.kind;

  it("renders the content of the one unanswered call for that very file", () => {
    const shown = onScreen(pane, [write(2, "t1", "/w/sub/a.txt", "one\n++two\n")]);
    expect(added(shown)).toEqual(["/w/sub/a.txt", "@@ -0,0 +1,2 @@\n+one\n+++two\n"]);
    expect(shown.whole).toBe(true);
    expect(onScreen(pane, [write(10, "t1", "/w/sub/a.txt", "late")]).whole).toBe(true);
  });

  it("renders a write whose request carries its content", () => {
    const shown = asAsked(approve("Write", { file_path: "/w/a.txt", content: "x" }));
    expect(added(shown)).toEqual(["/w/a.txt", "@@ -0,0 +1,1 @@\n+x\n"]);
    expect(shown.whole).toBe(true);
  });

  it("is locked where the content is not certain", () => {
    const cases: [string, EventRecord[]][] = [
      ["another directory", [write(2, "t1", "/w/other/a.txt", "x")]],
      ["answered", [write(2, "t1", "/w/sub/a.txt", "x"), done(3, "t1")]],
      ["two unanswered", [write(2, "t1", "/w/sub/a.txt", "x"), write(3, "t2", "/w/sub/a.txt", "y")]],
      ["a later write of that name", [write(2, "t1", "/w/sub/a.txt", "x"), write(10, "t2", "/w/sub/a.txt", "y"), done(11, "t2")]],
      ["another file of that name", [write(2, "t1", "/w/sub/a.txt", "x"), write(3, "t2", "/w/a.txt", "y")]],
      ["an edit's call", [write(2, "t1", "/w/sub/a.txt", "x", "Edit")]],
    ];
    for (const [name, events] of cases) {
      const shown = onScreen(pane, events);
      expect([name, shown.body.kind, shown.whole]).toEqual([name, "edit", false]);
    }
    const events = [write(2, "t1", "/w/sub/a.txt", "x"), pane];
    expect(shownRequest(pane, events, { cwd: "", fromScreen: true }).whole).toBe(false);
    expect(onScreen(pane, [write(2, "t1", "/w/sub/a.txt", "")]).whole).toBe(false);
  });

  it("is locked where the dialog names only the file and another folder has one too", () => {
    const named = approve("Write", { file_path: "a.txt" });
    expect(onScreen(named, [write(2, "t1", "/w/sub/a.txt", "long"), write(3, "t2", "/w/a.txt", "short")]).whole).toBe(false);
    expect(onScreen(named, [write(2, "t1", "/w/sub/a.txt", "long")]).whole).toBe(false);
  });

  it("locks an edit without its change", () => {
    const events = [write(2, "t1", "/w/a.txt", "x", "Edit")];
    expect(onScreen(approve("Edit", { file_path: "a.txt" }), events).whole).toBe(false);
    expect(asAsked(approve("Edit", { file_path: "/w/a.txt", old_string: "a", new_string: "b" })).whole).toBe(false);
  });
});

describe("the whole request", () => {
  it("lists every field the agent attached, new ones included, under its title", () => {
    const request = ev(1, "approval_request", "Approve command: ls", {
      method: "item/commandExecution/requestApproval",
      approval_id: "a1",
      item_id: "i1",
      status: "waiting_input",
      version: 2,
      interaction: { title: "Approve command: ls" },
      request: { command: "ls", reason: "why" },
      something_new: { x: 1 },
    });
    expect(asAsked(request).fields).toEqual({
      title: "Approve command: ls",
      request: { command: "ls", reason: "why" },
      something_new: { x: 1 },
    });
    expect(asAsked(request).body).toEqual({ kind: "command", description: "", command: "ls" });
  });

  const warnings = (input: Record<string, unknown> | null, request?: Record<string, unknown>) =>
    asAsked(approve("Bash", input, request ? { request } : {})).warnings;

  it("says what is asked beyond the call", () => {
    expect(warnings({ command: "ls", dangerouslyDisableSandbox: true })).toEqual(["Runs outside the sandbox"]);
    const network = "Extra permissions or network access";
    expect(warnings(null, { command: "ls", networkApprovalContext: { host: "x" } })).toEqual([network]);
    expect(warnings(null, { command: "ls", additionalPermissions: { network: {} } })).toEqual([network]);
    expect(warnings(null, { permissions: { network: {} } })).toEqual([network]);
    expect(warnings(null, { grantRoot: "/srv" })).toEqual(["Write access for the whole session"]);
    const every = "Replaces every occurrence";
    expect(warnings({ replace_all: true })).toEqual([every]);
    expect(warnings({ replaceAll: true })).toEqual([every]);
    expect(warnings({ edits: [{ replace_all: false }, { replace_all: true }] })).toEqual([every]);
    const elsewhere = "Different working directory";
    for (const input of [{ cwd: "/srv" }, { workdir: "../etc" }, { isolation: "worktree" }]) {
      expect(warnings(input)).toEqual([elsewhere]);
    }
    expect(warnings(null, { command: "ls", cwd: "/srv" })).toEqual([elsewhere]);
  });

  it("says nothing of an ordinary call", () => {
    expect(warnings({ command: "ls", replace_all: false, workdir: "/w/" })).toEqual([]);
    expect(warnings(null, { command: "ls", cwd: "/w", additionalPermissions: null, allowedPrompts: [] })).toEqual([]);
  });
});

describe("what holds the content", () => {
  const diff = (file: Record<string, unknown>, truncated = false) =>
    asAsked(
      ev(1, "approval_request", "Approve", {
        diff_preview: { phase: "proposed", truncated, files: [{ path: "a.ts", diff: "@@ -1 +1 @@\n-a\n+b\n", ...file }] },
      }),
    ).whole;
  const whole = (tool: string | null, input: Record<string, unknown> | null) =>
    asAsked(ev(1, "approval_request", "Approve", { ...(tool ? { tool_name: tool } : {}), ...(input ? { tool_input: input } : {}) }))
      .whole;

  it("takes a view that shows everything asked", () => {
    expect(diff({})).toBe(true);
    expect(whole("ExitPlanMode", { plan: "# Do it" })).toBe(true);
    expect(whole("Bash", { command: "ls" })).toBe(true);
    expect(whole("mcp__mail__send", { to: "a@b" })).toBe(true);
    // A tool without arguments is its name.
    expect(whole("mcp__x__ping", {})).toBe(true);
  });

  it("refuses a request with nothing of what it asks", () => {
    expect(whole("ExitPlanMode", { plan: "  " })).toBe(false);
    expect(whole("Bash", { command: "" })).toBe(false);
    expect(whole("Bash", { timeout: 5 })).toBe(false);
    expect(whole("Task", { prompt: "", description: "x" })).toBe(false);
    expect(whole("Workflow", { script: "" })).toBe(false);
    expect(whole("mcp__mail__send", { description: "" })).toBe(false);
    expect(whole(null, null)).toBe(false);
  });

  it("refuses a diff that is cut or lists a file without its content", () => {
    expect(diff({}, true)).toBe(false);
    expect(diff({ truncated: true })).toBe(false);
    expect(diff({ diff: "" })).toBe(false);
    expect(diff({ binary: true })).toBe(false);
    expect(diff({ unavailable_reason: "diff content was not included by backend" })).toBe(false);
    // Nothing of these would show, or not as the change is.
    expect(diff({ diff: "@@ fn\n-a\n+b\n" })).toBe(false);
    expect(diff({ diff: "@@ -1 +1 @@\n-a = 1+a = 2\n" })).toBe(false);
  });

  it("takes a file that only moved", () => {
    const moved = { old_path: "b.ts", change_type: "move" };
    expect(diff({ ...moved, diff: "" })).toBe(true);
    expect(diff({ ...moved, diff: "diff --git a/b.ts b/a.ts\nsimilarity index 100%\nrename from b.ts\nrename to a.ts\n" })).toBe(true);
    expect(diff({ ...moved, diff: "\n\nMoved to: a.ts" })).toBe(true);
    expect(diff({ ...moved, diff: "\n\nMoved to: c.ts" })).toBe(false);
    for (const other of [
      { diff: "similarity index 90%\nrename from b.ts\nrename to a.ts\n" },
      { diff: "old mode 100644\nnew mode 100755\nrename from b.ts\n" },
      { diff: "", additions: 2 },
      { diff: "", truncated: true },
      { diff: "", binary: true },
    ]) {
      expect([other, diff({ ...moved, ...other })]).toEqual([other, false]);
    }
    expect(diff({ old_path: "a.ts", diff: "" })).toBe(false);
  });
});
