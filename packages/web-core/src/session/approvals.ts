import { supportsPlanApproval, type BackendCatalog } from "../backends";
import {
  asRecord,
  FILE_EDIT_TOOLS,
  parseEvent,
  planForEvent,
  readSummary,
  readToolName,
  toolFilePath,
  type EventDiffPreview,
  type PlanDecision,
  type PlanViewModel,
} from "../events";
import type { EventRecord, SessionRecord } from "../types";
import { collapseSupersededPlanEvents, latestActionablePlanEvent } from "./transcript";

export function findPendingApprovals(events: EventRecord[]): EventRecord[] {
  // Track approvals as a queue: every approval_request enqueues, and every
  // resolution note (response sent, timed out, invalidated) dequeues its
  // matching entry (by approval_id, falling back to oldest-first).
  const queue: EventRecord[] = [];
  for (const event of events) {
    if (event.kind === "approval_request") {
      queue.push(event);
    } else if (event.kind === "system_note" && isApprovalResolutionEvent(event)) {
      const approvalId = event.metadata?.approval_id;
      if (typeof approvalId === "string") {
        const index = queue.findIndex((e) => e.metadata?.approval_id === approvalId);
        if (index !== -1) queue.splice(index, 1);
      } else {
        queue.shift();
      }
    }
  }
  return queue;
}

function isApprovalResolutionEvent(event: EventRecord): boolean {
  if (event.metadata?.method === "approval.invalidated") {
    return true;
  }
  return /(Approval response sent|Approval timed out)/i.test(event.text);
}

// ─── Approval cards ───

export type ApprovalBody =
  | { kind: "diff"; prompt: string; preview: EventDiffPreview }
  | { kind: "plan"; plan: string }
  | { kind: "task"; prompt: string; description: string; subagent: string }
  | { kind: "command"; description: string; command: string }
  | { kind: "workflow"; script: string }
  | { kind: "edit"; tool: string; path: string | null }
  | { kind: "raw"; text: string };

export const WORKFLOW_TOOLS: ReadonlySet<string> = new Set(["Workflow", "RunWorkflow"]);

const text = (value: unknown): string => (typeof value === "string" ? value : "");

export function approvalBody(event: EventRecord): ApprovalBody {
  const preview = parseEvent(event).diffPreview;
  if (preview) return { kind: "diff", prompt: event.text, preview };
  const tool = readToolName(event);
  const input = asRecord(event.metadata?.tool_input);
  const described = readSummary(event) ?? "";
  if (tool === "ExitPlanMode" && typeof input?.plan === "string") return { kind: "plan", plan: input.plan };
  if ((tool === "Task" || tool === "Agent") && typeof input?.prompt === "string") {
    return { kind: "task", prompt: input.prompt, description: described, subagent: text(input.subagent_type) };
  }
  if (tool === "Bash" && typeof input?.command === "string") {
    return { kind: "command", description: described, command: input.command };
  }
  if (tool && WORKFLOW_TOOLS.has(tool) && typeof input?.script === "string") {
    return { kind: "workflow", script: input.script };
  }
  if (tool && FILE_EDIT_TOOLS.has(tool)) return { kind: "edit", tool, path: toolFilePath(input) };
  return { kind: "raw", text: event.text };
}

export function approvalPrompt(body: ApprovalBody): string | null {
  switch (body.kind) {
    case "diff":
      return body.prompt;
    case "plan":
      return "Approve plan and exit plan mode";
    case "task":
      return `Approve subagent task${body.description ? `: ${body.description}` : ""}${body.subagent ? ` (via ${body.subagent})` : ""}`;
    case "command":
      return `Approve Bash command${body.description ? `: ${body.description}` : ""}`;
    case "workflow":
      return "Review this dynamic workflow before it runs";
    case "edit":
      return `Approve ${body.tool}${body.path ? ` on ${body.path}` : ""}`;
    case "raw":
      return null;
  }
}

export function approvalCopyText(body: ApprovalBody, eventText: string): string {
  switch (body.kind) {
    case "plan":
      return body.plan;
    case "task":
      return body.prompt;
    case "command":
      return body.command;
    case "workflow":
      return body.script;
    default:
      return eventText;
  }
}

export function approvalId(event: EventRecord): string | undefined {
  const id = event.metadata?.approval_id;
  return typeof id === "string" ? id : undefined;
}

export interface ApprovalAction<Id extends string = string> {
  id: Id;
  label: string;
  primary: boolean;
  // Shown on the button while its decision is in flight.
  loadingLabel?: string;
}

// Approve, Decline and Cancel are universal; the escalations show only when the
// agent honours them.
export function approvalActions(decisions: readonly string[] | undefined): ApprovalAction[] {
  const allowed = new Set(decisions ?? []);
  return [
    { id: "accept", label: "Approve", primary: true },
    ...(allowed.has("acceptForSession") ? [{ id: "acceptForSession", label: "Approve for session", primary: false }] : []),
    ...(allowed.has("acceptAlways") ? [{ id: "acceptAlways", label: "Always allow", primary: false }] : []),
    { id: "decline", label: "Decline", primary: false },
    { id: "cancel", label: "Cancel", primary: false },
  ];
}

const PLAN_ACTIONS: ReadonlyArray<ApprovalAction<PlanDecision>> = [
  { id: "accept", label: "Approve", loadingLabel: "Approving…", primary: true },
  { id: "acceptForSession", label: "Approve for session", loadingLabel: "Approving…", primary: false },
  { id: "decline", label: "Decline", loadingLabel: "Declining…", primary: false },
  { id: "cancel", label: "Cancel", loadingLabel: "Cancelling…", primary: false },
];

export function planApprovalActions(decisions: readonly PlanDecision[]): ApprovalAction<PlanDecision>[] {
  return PLAN_ACTIONS.filter((action) => decisions.includes(action.id));
}

// The plan waiting for the human: the session is in plan mode, its agent takes plan
// approvals, and the newest plan has no decision yet.
export function pendingPlanApproval(
  session: SessionRecord | null,
  catalog: BackendCatalog | undefined,
  events: EventRecord[],
): { event: EventRecord; plan: PlanViewModel } | null {
  if (session?.permission_mode !== "plan" || !supportsPlanApproval(session.backend, catalog)) return null;
  const event = latestActionablePlanEvent(collapseSupersededPlanEvents(events));
  const plan = event ? planForEvent(event) : null;
  return event && plan ? { event, plan } : null;
}
