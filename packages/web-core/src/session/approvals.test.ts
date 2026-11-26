import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import type { EventRecord, SessionRecord } from "../types";
import {
  approvalActions,
  approvalBody,
  approvalBodyForTool,
  approvalCopyText,
  approvalId,
  approvalPrompt,
  findPendingApprovals,
  pendingPlanApproval,
  planApprovalActions,
  wholeApprovalBody,
  holdsWholeRequest,
  type ApprovalBody,
  shownRequest,
  type ShownRequest,
} from "./approvals";

const request = (seq: number, approvalId?: string) =>
  ev(seq, "approval_request", "allow?", approvalId ? { approval_id: approvalId } : {});
const resolved = (seq: number, approvalId?: string, text = "Approval response sent") =>
  ev(seq, "system_note", text, approvalId ? { approval_id: approvalId } : {});

describe("wholeApprovalBody", () => {
  const input = { to: "a@b.c", body: "x".repeat(400), cc: "d@e.f" };

  it("shows all of a generic tool's input where the request text is cut", () => {
    const cut = ev(1, "approval_request", `Approve mcp__mail__send: ${JSON.stringify(input).slice(0, 240)}`, {
      tool_name: "mcp__mail__send",
      tool_input: input,
    });
    const body = wholeApprovalBody(cut);
    expect(body).toEqual({ kind: "raw", text: JSON.stringify(input, null, 2) });
    expect(approvalCopyText(body, cut.text)).toBe(JSON.stringify(input, null, 2));
  });

  it("keeps the request text without input, and a body of its own", () => {
    const bare = ev(1, "approval_request", "Approve file changes", {});
    expect(wholeApprovalBody(bare)).toEqual({ kind: "raw", text: "Approve file changes" });
    const command = ev(2, "approval_request", "Approve Bash", { tool_name: "Bash", tool_input: { command: "ls" } });
    expect(wholeApprovalBody(command)).toEqual(approvalBody(command));
  });
});

describe("wholeApprovalBody of a request with a command beside its title", () => {
  it("shows the command", () => {
    const event = ev(1, "approval_request", "Approve command: git push", { request: { command: "git push" } });
    expect(wholeApprovalBody(event)).toEqual({ kind: "command", description: "", command: "git push" });
    const none = ev(2, "approval_request", "Approve command: ", { request: { command: null } });
    expect(wholeApprovalBody(none)).toEqual({ kind: "raw", text: "Approve command: " });
  });
});

describe("holdsWholeRequest", () => {
  const diff = (file: Record<string, unknown>, truncated = false): ApprovalBody =>
    approvalBody(
      ev(1, "approval_request", "Approve", {
        diff_preview: { phase: "proposed", truncated, files: [{ path: "a.ts", diff: "@@ -1 +1 @@\n-a\n+b\n", ...file }] },
      }),
    );

  it("takes a body that shows everything asked", () => {
    expect(holdsWholeRequest(diff({}), null)).toBe(true);
    expect(holdsWholeRequest({ kind: "plan", plan: "# Do it" }, null)).toBe(true);
    expect(holdsWholeRequest({ kind: "command", description: "", command: "ls" }, null)).toBe(true);
    expect(holdsWholeRequest({ kind: "raw", text: "{}" }, { to: "a@b" })).toBe(true);
    // A tool without arguments is its name.
    expect(holdsWholeRequest({ kind: "raw", text: "Approve mcp__x__ping" }, {})).toBe(true);
  });

  it("refuses a body with nothing in it", () => {
    expect(holdsWholeRequest({ kind: "plan", plan: "  " }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "command", description: "", command: "" }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "task", prompt: "", description: "x", subagent: "" }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "workflow", script: "" }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "edit", tool: "Edit", path: "/srv/a.ts" }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "raw", text: "{}" }, { description: "" })).toBe(false);
  });

  it("refuses a request known only by its title or method", () => {
    expect(holdsWholeRequest({ kind: "raw", text: "Approve file changes" }, null)).toBe(false);
    expect(holdsWholeRequest({ kind: "raw", text: "Approve plan" }, {}, "ExitPlanMode")).toBe(false);
    expect(holdsWholeRequest({ kind: "raw", text: "{}" }, { timeout: 5 }, "Bash")).toBe(false);
  });

  it("refuses a diff that is cut or lists a file without its content", () => {
    expect(holdsWholeRequest(diff({}, true), null)).toBe(false);
    expect(holdsWholeRequest(diff({ truncated: true }), null)).toBe(false);
    expect(holdsWholeRequest(diff({ diff: "" }), null)).toBe(false);
    expect(holdsWholeRequest(diff({ binary: true }), null)).toBe(false);
    expect(holdsWholeRequest(diff({ unavailable_reason: "diff content was not included by backend" }), null)).toBe(false);
    // Nothing of these would show.
    expect(holdsWholeRequest(diff({ diff: "rename from a\nrename to b\n" }), null)).toBe(false);
    expect(holdsWholeRequest(diff({ diff: "@@ fn\n-a\n+b\n" }), null)).toBe(false);
  });
});

describe("shownRequest", () => {
  const approve = (input: Record<string, unknown>, tool = "Write") =>
    ev(9, "approval_request", `Approve ${tool}`, { tool_name: tool, tool_input: input, approval_id: "a1" });
  const call = (seq: number, id: string, path: string, content: string, tool = "Write") =>
    ev(seq, "tool_call", tool, { tool_name: tool, tool_use_id: id, payload: { input: { file_path: path, content } } });
  const added = (request: ShownRequest) =>
    request.body.kind === "diff" ? [request.body.preview.files[0].path, request.body.preview.files[0].diff] : request.body.kind;
  const pending = approve({ file_path: "sub/a.txt" });
  const spoke = ev(1, "user_input", "go");

  it("renders a write from the one unanswered call for that file", () => {
    const shown = shownRequest(pending, [spoke, call(2, "t1", "/w/sub/a.txt", "one\n++two\n")], "/w");
    expect(added(shown)).toEqual(["/w/sub/a.txt", "@@ -0,0 +1,2 @@\n+one\n+++two\n"]);
    expect(shown.whole).toBe(true);
  });

  it("renders a write whose request carries its content", () => {
    const shown = shownRequest(approve({ file_path: "/w/a.txt", content: "x" }), [], "");
    expect(added(shown)).toEqual(["/w/a.txt", "@@ -0,0 +1,1 @@\n+x\n"]);
    expect(shown.whole).toBe(true);
  });

  it("locks a write whose content is not certain", () => {
    const result = ev(3, "tool_result", "done", { tool_use_id: "t1" });
    const cases: [string, EventRecord[], string][] = [
      ["another directory", [spoke, call(2, "t1", "/w/other/a.txt", "x")], "/w"],
      ["answered", [spoke, call(2, "t1", "/w/sub/a.txt", "x"), result], "/w"],
      ["two candidates", [spoke, call(2, "t1", "/w/sub/a.txt", "x"), call(3, "t2", "/w/sub/a.txt", "y")], "/w"],
      ["from before the human spoke", [call(1, "t1", "/w/sub/a.txt", "x"), ev(2, "user_input", "next")], "/w"],
      ["after the request", [spoke, call(10, "t1", "/w/sub/a.txt", "x")], "/w"],
      ["unknown cwd", [spoke, call(2, "t1", "/w/sub/a.txt", "x")], ""],
      ["an edit's call", [spoke, call(2, "t1", "/w/sub/a.txt", "x", "Edit")], "/w"],
    ];
    for (const [name, events, cwd] of cases) {
      const shown = shownRequest(pending, events, cwd);
      expect([name, shown.body.kind, shown.whole]).toEqual([name, "edit", false]);
    }
  });

  it("locks an edit, and a request known by its title", () => {
    const events = [spoke, call(2, "t1", "/w/a.txt", "x", "Edit")];
    expect(shownRequest(approve({ file_path: "a.txt" }, "Edit"), events, "/w").whole).toBe(false);
    expect(shownRequest(ev(1, "approval_request", "Approve file changes", {}))).toEqual({
      body: { kind: "raw", text: "Approve file changes" },
      whole: false,
    });
  });
});

describe("findPendingApprovals", () => {
  it("is empty without events", () => {
    expect(findPendingApprovals([])).toEqual([]);
  });

  it("queues approval requests in arrival order", () => {
    const events = [request(1, "a"), ev(2, "agent_output", "x"), request(3, "b")];
    expect(findPendingApprovals(events).map((e) => e.sequence)).toEqual([1, 3]);
  });

  it("dequeues the matching approval id, wherever it sits in the queue", () => {
    const events = [request(1, "a"), request(2, "b"), request(3, "c"), resolved(4, "b")];
    expect(findPendingApprovals(events).map((e) => e.metadata.approval_id)).toEqual(["a", "c"]);
  });

  it("dequeues the oldest request when the resolution carries no approval id", () => {
    const events = [request(1, "a"), request(2, "b"), resolved(3)];
    expect(findPendingApprovals(events).map((e) => e.metadata.approval_id)).toEqual(["b"]);
  });

  it("leaves the queue alone when the resolution id matches nothing", () => {
    const events = [request(1, "a"), resolved(2, "zzz")];
    expect(findPendingApprovals(events)).toHaveLength(1);
  });

  it("treats timeouts and invalidations as resolutions", () => {
    expect(findPendingApprovals([request(1, "a"), resolved(2, "a", "Approval timed out")])).toEqual([]);
    const invalidated = ev(2, "system_note", "gone", { approval_id: "a", method: "approval.invalidated" });
    expect(findPendingApprovals([request(1, "a"), invalidated])).toEqual([]);
  });

  it("ignores resolution wording on non-system events and unrelated notes", () => {
    const events = [request(1, "a"), ev(2, "agent_output", "Approval response sent"), resolved(3, "a", "something else")];
    expect(findPendingApprovals(events)).toHaveLength(1);
  });

  it("is a no-op for a resolution that arrives with nothing queued", () => {
    expect(findPendingApprovals([resolved(1)])).toEqual([]);
  });

  it("matches resolution wording case-insensitively", () => {
    expect(findPendingApprovals([request(1, "a"), resolved(2, "a", "APPROVAL TIMED OUT")])).toEqual([]);
  });
});

const approval = (toolName: string, toolInput: Record<string, unknown>, extra: Record<string, unknown> = {}) =>
  ev(1, "approval_request", "raw text", { tool_name: toolName, tool_input: toolInput, approval_id: "ap1", ...extra });

describe("approvalBodyForTool", () => {
  it("reads a request from the tool call alone", () => {
    expect(approvalBodyForTool("Bash", { command: "ls" }, "Run a command")).toEqual({ kind: "command", description: "", command: "ls" });
    expect(approvalBodyForTool("Edit", { file_path: "/a.ts" }, "Edit")).toEqual({ kind: "edit", tool: "Edit", path: "/a.ts" });
    expect(approvalBodyForTool(null, null, "Allow the thing")).toEqual({ kind: "raw", text: "Allow the thing" });
  });
});

describe("approvalBody", () => {
  it("reads each tool's body and prompt", () => {
    const plan = approvalBody(approval("ExitPlanMode", { plan: "# Plan" }));
    expect(plan).toEqual({ kind: "plan", plan: "# Plan" });
    expect(approvalPrompt(plan)).toBe("Approve plan and exit plan mode");

    const task = approvalBody(approval("Agent", { prompt: "go", description: "Audit", subagent_type: "Explore" }));
    expect(approvalPrompt(task)).toBe("Approve subagent task: Audit (via Explore)");

    const bash = approvalBody(approval("Bash", { command: "ls", description: "List" }));
    expect(bash).toEqual({ kind: "command", description: "List", command: "ls" });
    expect(approvalPrompt(bash)).toBe("Approve Bash command: List");

    const workflow = approvalBody(approval("Workflow", { script: "x()" }));
    expect(approvalPrompt(workflow)).toBe("Review this dynamic workflow before it runs");

    const edit = approvalBody(approval("Edit", { file_path: "/a.ts" }));
    expect(edit).toEqual({ kind: "edit", tool: "Edit", path: "/a.ts" });
    expect(approvalPrompt(edit)).toBe("Approve Edit on /a.ts");

    const raw = approvalBody(approval("Mystery", {}));
    expect(raw).toEqual({ kind: "raw", text: "raw text" });
    expect(approvalPrompt(raw)).toBeNull();
  });

  it("prefers a diff preview over the tool body", () => {
    const event = approval("Edit", { file_path: "/a.ts" }, {
      diff_preview: { files: [{ path: "/a.ts", hunks: [] }], truncated: false },
    });
    const body = approvalBody(event);
    expect(body.kind === "diff" ? body.prompt : null).toBe("raw text");
  });

  it("copies the command, plan, prompt or script, else the text", () => {
    const copy = (event: ReturnType<typeof approval>) => approvalCopyText(approvalBody(event), event.text);
    expect(copy(approval("Bash", { command: "ls" }))).toBe("ls");
    expect(copy(approval("ExitPlanMode", { plan: "p" }))).toBe("p");
    expect(copy(approval("Task", { prompt: "go" }))).toBe("go");
    expect(copy(approval("Mystery", {}))).toBe("raw text");
    expect(approvalId(approval("Bash", { command: "ls" }))).toBe("ap1");
  });
});

describe("approval actions", () => {
  it("shows escalations only when the agent lists them", () => {
    expect(approvalActions(["approve", "decline"]).map((a) => a.id)).toEqual(["accept", "decline", "cancel"]);
    expect(approvalActions(["acceptForSession", "acceptAlways"]).map((a) => a.id)).toEqual([
      "accept",
      "acceptForSession",
      "acceptAlways",
      "decline",
      "cancel",
    ]);
    expect(approvalActions(undefined).filter((a) => a.primary).map((a) => a.id)).toEqual(["accept"]);
  });

  it("offers the plan's decisions in a fixed order", () => {
    expect(planApprovalActions(["decline", "accept"]).map((a) => a.id)).toEqual(["accept", "decline"]);
    expect(planApprovalActions(["accept", "acceptForSession", "decline", "cancel"])).toHaveLength(4);
  });
});

describe("pendingPlanApproval", () => {
  const plan = ev(2, "agent_output", "plan", planMetadata("p1", "Do it"));
  const session = (mode: string, backend = "codex") => ({ permission_mode: mode, backend }) as SessionRecord;

  it("is the undecided plan of a plan-mode session whose agent takes plan approvals", () => {
    const found = pendingPlanApproval(session("plan"), undefined, [ev(1, "user_input", "go"), plan]);
    expect(found?.event).toBe(plan);
    expect(found?.plan.id).toBe("p1");
  });

  it("is null outside plan mode, or for an agent without plan approval", () => {
    expect(pendingPlanApproval(session("default"), undefined, [plan])).toBeNull();
    expect(pendingPlanApproval(session("plan", "claude_code"), undefined, [plan])).toBeNull();
    expect(pendingPlanApproval(null, undefined, [plan])).toBeNull();
  });
});
