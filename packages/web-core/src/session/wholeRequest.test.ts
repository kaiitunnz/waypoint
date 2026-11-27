import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import type { EventRecord } from "../types";
import { approvalCard, requestText, shownRequest, titlingSummary } from "./wholeRequest";

const ownInput = { cwd: "/w", fromScreen: false };
const screen = { cwd: "/w", fromScreen: true };

const approve = (tool: string, input: Record<string, unknown> | null, extra: Record<string, unknown> = {}) =>
  ev(9, "approval_request", `Approve ${tool}`, {
    tool_name: tool,
    ...(input ? { tool_input: input } : {}),
    approval_id: "a1",
    ...extra,
  });
const asAsked = (request: EventRecord) => shownRequest(request, ownInput);

describe("a request read off the screen", () => {
  const unverified = ["This call can't be verified from here"];

  it("is never decided, whatever it names", () => {
    for (const request of [
      approve("Bash", { command: "sleep 60" }),
      approve("Write", { file_path: "a.txt", content: "x" }),
      approve("Agent", { description: "Sweep", prompt: "Delete the caches" }),
      approve("mcp__mail__send", { to: "a@b" }),
    ]) {
      const shown = shownRequest(request, screen);
      expect(shown).toMatchObject({ unverified: true, whole: false, warnings: unverified });
      expect(shown.fields).toMatchObject({ tool_input: request.metadata.tool_input });
    }
  });

  it("is not decided where it is a plan, which may differ from the one in the dialog", () => {
    for (const text of ["# Do it", ""]) {
      const request = approve("ExitPlanMode", { plan: text }, { interaction: { kind: "plan_approval" } });
      expect(shownRequest(request, screen)).toMatchObject({ unverified: true, whole: false, warnings: unverified, lockNote: null });
    }
  });

  it("is otherwise as the agent gave it", () => {
    expect(asAsked(approve("Bash", { command: "ls" }))).toMatchObject({ unverified: false, whole: true, warnings: [] });
  });
});

describe("a write", () => {
  it("is locked without its diff", () => {
    for (const input of [{ file_path: "/w/a.txt" }, { file_path: "/w/a.txt", content: "x" }]) {
      expect(asAsked(approve("Write", input))).toMatchObject({ body: { kind: "edit" }, whole: false });
    }
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

describe("hidden characters", () => {
  const hidden = "Hidden or direction-changing characters";

  it("are written out, so the JSON still reads as the request", () => {
    const fields = { command: "echo a\u202eb\u200bc\u{e0001} # d\u2028e", note: "tab\there\nnext" };
    const text = requestText(fields);
    expect(text).toContain("a\\u202eb\\u200bc\\udb40\\udc01 # d\\u2028e");
    expect(JSON.parse(text)).toEqual(fields);
  });

  it("include what draws nothing without being a control or format character", () => {
    const fields = { command: "a\ufe0fb\u034fc\u3164d\u{e0100}" };
    expect(requestText(fields)).toContain("a\\ufe0fb\\u034fc\\u3164d\\udb40\\udd00");
    expect(asAsked(approve("Bash", { command: "ls\u3164" })).warnings).toEqual([hidden]);
  });

  it("are warned of wherever the request carries them, a diff included", () => {
    expect(asAsked(approve("Bash", { command: "ls \u202e# x" })).warnings).toEqual([hidden]);
    expect(asAsked(approve("Bash", { command: "ls\u001b[8mrm -rf a" })).warnings).toEqual([hidden]);
    const preview = { phase: "proposed", truncated: false, files: [{ path: "a.ts", diff: "@@ -1 +1 @@\n-a\n+b\u2066\n" }] };
    expect(asAsked(ev(1, "approval_request", "Approve", { diff_preview: preview })).warnings).toEqual([hidden]);
    expect(asAsked(approve("Bash", { command: "a\n\tb c" })).warnings).toEqual([]);
    expect(asAsked(approve("Write", { file_path: "a.txt", content: "a\r\nb\r\n" })).warnings).toEqual([]);
  });

  it("include blanks that read as a space, written out in a command that is still decided", () => {
    // No word ends at a no-break space, so `#` starts no comment and the rest runs.
    for (const [blank, written] of [
      ["\u00a0", "\\u00a0"],
      ["\u3000", "\\u3000"],
      ["\u2800", "\\u2800"],
    ]) {
      const shown = asAsked(approve("Bash", { command: `echo cleanup${blank}#; rm -rf build` }));
      expect(shown).toMatchObject({ warnings: [hidden], whole: true });
      expect(requestText(shown.fields)).toContain(`cleanup${written}#`);
    }
  });

  it("include any carriage return in a command, a CR LF's too", () => {
    // The backslash escapes the return, so the next line is a command of its own.
    const command = "echo would run: \\\r\nrm -rf build";
    for (const request of [approve("Bash", { command }), approve("bash", null, { request: { command } })]) {
      const shown = asAsked(request);
      expect(shown).toMatchObject({ warnings: [hidden], whole: true });
      expect(requestText(shown.fields)).toContain("\\\\\\r\\nrm");
    }
  });

  const changed = (file: Record<string, unknown>) => {
    const preview = { phase: "proposed", truncated: false, files: [{ path: "a.ts", diff: "@@ -1 +1 @@\n-a\n+b\n", ...file }] };
    return asAsked(ev(1, "approval_request", "Approve", { diff_preview: preview }));
  };
  // Unchanged first line, then one line changed.
  const under = (line: string) => ({ diff: `@@ -1,2 +1,2 @@\n ${line}\n-a\n+b\n` });

  it("lock a diff that hides them in a path or a changed line", () => {
    const locked = { whole: false, lockNote: "The diff can't show its hidden characters", warnings: [hidden] };
    for (const file of [
      { diff: "@@ -1 +1 @@\n-a\n+if (x) {\u202e} \u2066// admin\u2069\n" },
      { diff: "@@ -1 +1 @@\n-a\u200b\n+a\n" },
      { diff: "@@ -1 +1 @@\n-a\n+\u26a0\ufe0f a\n" },
      { diff: "@@ -1 +1 @@\n-a\n+\ufeffa\n" },
      { diff: "@@ -1 +1 @@\n-a\n+a\rb\n" },
      { path: "a\u202egnp.ts" },
      { old_path: "b\u3164.ts" },
    ]) {
      expect([file, changed(file)]).toMatchObject([file, locked]);
    }
  });

  it("lock a diff whose unchanged line can reorder what is around it", () => {
    for (const control of ["\u202e", "\u2066", "\u200f", "\u061c"]) expect(changed(under(`x${control}y`)).whole).toBe(false);
  });

  it("leave a diff to be decided where they neither change nor reorder it", () => {
    for (const file of [under("\ufeffimport os"), under("\u26a0\ufe0f Warning"), under("a\u200bb")]) {
      expect([file, changed(file)]).toMatchObject([file, { whole: true, lockNote: null, warnings: [hidden] }]);
    }
    // A blank that reads as a space is warned of only.
    expect(changed({ diff: "@@ -1 +1 @@\n-a\n+a\u00a0b\n" })).toMatchObject({ whole: true, warnings: [hidden] });
    expect(changed({ diff: "@@ -1 +1 @@\r\n-a\r\n+\tb\r\n" })).toMatchObject({ whole: true, warnings: [] });
  });

  it("leave a request the raw block writes out to be decided", () => {
    expect(asAsked(approve("Bash", { command: "ls \u202e# x" }))).toMatchObject({ whole: true, lockNote: null });
  });
});

describe("a workflow", () => {
  it("is locked where another script runs in place of the one given", () => {
    expect(asAsked(approve("Workflow", { script: "run()" })).whole).toBe(true);
    expect(asAsked(approve("Workflow", { script: "run()", scriptPath: "/tmp/w.js" })).whole).toBe(false);
    expect(asAsked(approve("Workflow", { script: "run()", name: "saved" })).whole).toBe(false);
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

  it("says why it refuses", () => {
    expect(asAsked(approve("ExitPlanMode", { plan: "" })).lockNote).toBe("No plan text");
    expect(asAsked(approve("Bash", { command: "" })).lockNote).toBe("The whole request isn't available here");
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

const described = (tool: string, input: Record<string, unknown>, summary: string, extra: Record<string, unknown> = {}) =>
  approve(tool, input, { summary, ...extra });

// A Codex command approval: no tool or input, its command and parsed action in `request`.
const codex = (extra: Record<string, unknown> = {}, summary = "Read `SKILL.md`") =>
  ev(9, "approval_request", "Approve command: sed -n 1,5p SKILL.md", {
    method: "item/commandExecution/requestApproval",
    approval_id: "a1",
    summary,
    request: { command: "sed -n 1,5p SKILL.md", cwd: "/w", reason: "needed", ...extra },
  });

describe("the summary that titles an approval", () => {
  const bash = { command: "pytest -q", description: "Run the tests" };

  it("stands for a verified call that asks for nothing beyond itself", () => {
    expect(approvalCard(described("Bash", bash, "Run the tests"), "/w")).toEqual({
      title: "Run the tests",
      marked: true,
      subagent: null,
      body: { kind: "command", description: "Run the tests", command: "pytest -q" },
    });
    expect(titlingSummary(described("Bash", bash, "Run the tests", { call_verified: true }).metadata, "/w")).toBe("Run the tests");
  });

  it("gives way to the plain title without one", () => {
    expect(approvalCard(approve("Bash", bash), "/w")).toMatchObject({ title: "Approve Bash command", marked: false, subagent: null });
  });

  it("gives way for an unverified call, with the agent's words nowhere in the title", () => {
    const card = approvalCard(described("Bash", bash, "Run the tests", { call_verified: false }), "/w");
    expect(card).toMatchObject({ title: "Approve Bash command", marked: false });
  });

  it.each([
    ["a hidden character", "Run the ​tests"],
    ["a direction character", "Run the ‮tests"],
    ["a look-alike blank", "Run the tests"],
  ])("gives way when the summary holds %s", (_name, summary) => {
    expect(approvalCard(described("Bash", bash, summary), "/w")).toMatchObject({ title: "Approve Bash command", marked: false });
  });

  it.each([
    ["runs outside the sandbox", { dangerouslyDisableSandbox: true }],
    ["runs in another directory", { workdir: "/etc" }],
  ])("gives way for a call that %s", (_name, extra) => {
    expect(titlingSummary(described("Bash", { ...bash, ...extra }, "Run the tests").metadata, "/w")).toBeNull();
  });

  it.each([
    ["extra permissions", { additionalPermissions: { network: true } }],
    ["network access", { networkApprovalContext: { host: "example.com" } }],
    ["a session write grant", { grantRoot: "/w" }],
  ])("gives way for a Codex request that asks for %s", (_name, extra) => {
    const request = codex(extra);
    expect(approvalCard(request, "/w")).toMatchObject({ title: "Approve this request", marked: false });
    // The detail warns for the same reason the title gives way.
    expect(shownRequest(request, ownInput).warnings.length).toBeGreaterThan(0);
  });

  it("shows a summary-titled Codex command as the block under the title", () => {
    expect(approvalCard(codex(), "/w")).toEqual({
      title: "Read `SKILL.md`",
      marked: true,
      subagent: null,
      body: { kind: "command", description: "", command: "sed -n 1,5p SKILL.md" },
    });
    expect(shownRequest(codex(), ownInput)).toMatchObject({ body: { kind: "command" }, whole: true, warnings: [] });
    // Without a summary the dock keeps its text.
    expect(approvalCard(codex({}, ""), "/w").body).toMatchObject({ kind: "raw" });
  });

  it("shows the command of a summary-titled call without a view of its own", () => {
    const watch = { command: "tail -f build.log", description: "Watch the build", persistent: true };
    expect(approvalCard(described("Monitor", watch, "Watch the build"), "/w").body).toEqual({
      kind: "command",
      description: "",
      command: "tail -f build.log",
    });
    expect(approvalCard(approve("Monitor", watch), "/w").body).toMatchObject({ kind: "raw" });
  });

  it("names the subagent's kind beside a summary title", () => {
    const task = { prompt: "List the callers", description: "Find callers", subagent_type: "Explore" };
    expect(approvalCard(described("Agent", task, "Find callers"), "/w")).toMatchObject({ title: "Find callers", subagent: "Explore" });
    expect(approvalCard(approve("Agent", task), "/w")).toMatchObject({
      title: "Approve subagent task (via Explore)",
      subagent: null,
    });
  });

  it("keeps the summary out of the request's listed fields", () => {
    expect(shownRequest(described("Bash", bash, "Run the tests"), ownInput).fields).not.toHaveProperty("summary");
  });
});
