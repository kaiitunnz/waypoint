import { describe, expect, it } from "vitest";

import { ev } from "../test-helpers";
import type { EventRecord } from "../types";
import { shownRequest } from "./wholeRequest";

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

  it("is decided where it is a plan, which comes from its plan file", () => {
    const plan = (text: string) =>
      shownRequest(approve("ExitPlanMode", { plan: text }, { interaction: { kind: "plan_approval" } }), screen);
    expect(plan("# Do it")).toMatchObject({ unverified: false, whole: true });
    expect(plan("")).toMatchObject({ unverified: false, whole: false });
    // A tool dialog that only names itself a plan is not one.
    expect(shownRequest(approve("ExitPlanMode", { plan: "# Do it" }, { interaction: { kind: "approval" } }), screen).whole).toBe(
      false,
    );
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
