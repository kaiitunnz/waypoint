import { describe, expect, it } from "vitest";

import { ev, planMetadata } from "../test-helpers";
import type { SessionRecord } from "../types";
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
} from "./approvals";

const request = (seq: number, approvalId?: string) =>
  ev(seq, "approval_request", "allow?", approvalId ? { approval_id: approvalId } : {});
const resolved = (seq: number, approvalId?: string, text = "Approval response sent") =>
  ev(seq, "system_note", text, approvalId ? { approval_id: approvalId } : {});

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
