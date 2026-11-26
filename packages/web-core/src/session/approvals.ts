import { normalizeToolName, parseEvent, type EventDiffPreview, type PlanDecision } from "../events";
import type { EventRecord } from "../types";

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

export interface ApprovalPager {
  count: number;
  page: number;
  current: EventRecord | null;
}

// The queue can shrink under the page index (an approval resolves while the
// pager is on a later one), so the index is clamped on every read.
export function approvalPager(approvals: EventRecord[], index: number): ApprovalPager {
  const count = approvals.length;
  const page = count > 0 ? Math.min(index, count - 1) : 0;
  return { count, page, current: approvals[page] ?? null };
}

export function stepApprovalPage(index: number, direction: "prev" | "next", count: number): number {
  return direction === "prev" ? Math.max(0, index - 1) : Math.min(count - 1, index + 1);
}

// ─── Approval cards ───

// What an approval card shows, by tool.
export type ApprovalBody =
  | { kind: "diff"; prompt: string; preview: EventDiffPreview }
  | { kind: "plan"; plan: string }
  | { kind: "task"; prompt: string; description: string; subagent: string }
  | { kind: "command"; description: string; command: string }
  | { kind: "workflow"; script: string }
  | { kind: "edit"; tool: string; path: string | null }
  | { kind: "raw"; text: string };

const FILE_EDIT_APPROVAL_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);
const WORKFLOW_TOOLS = new Set(["Workflow", "RunWorkflow"]);

function approvalTool(event: EventRecord): { tool: string | null; input: Record<string, unknown> | null } {
  const metadata = event.metadata ?? {};
  const tool = normalizeToolName(typeof metadata.tool_name === "string" ? metadata.tool_name : null);
  const input =
    metadata.tool_input && typeof metadata.tool_input === "object"
      ? (metadata.tool_input as Record<string, unknown>)
      : null;
  return { tool, input };
}

const text = (value: unknown): string => (typeof value === "string" ? value : "");

export function approvalBody(event: EventRecord): ApprovalBody {
  const preview = parseEvent(event).diffPreview;
  if (preview) return { kind: "diff", prompt: event.text, preview };
  const { tool, input } = approvalTool(event);
  if (tool === "ExitPlanMode" && typeof input?.plan === "string") return { kind: "plan", plan: input.plan };
  if ((tool === "Task" || tool === "Agent") && typeof input?.prompt === "string") {
    return { kind: "task", prompt: input.prompt, description: text(input.description), subagent: text(input.subagent_type) };
  }
  if (tool === "Bash" && typeof input?.command === "string") {
    return { kind: "command", description: text(input.description), command: input.command };
  }
  if (tool && WORKFLOW_TOOLS.has(tool) && typeof input?.script === "string") {
    return { kind: "workflow", script: input.script };
  }
  if (tool && FILE_EDIT_APPROVAL_TOOLS.has(tool)) {
    const path = input?.file_path ?? input?.path ?? input?.notebook_path;
    return { kind: "edit", tool, path: typeof path === "string" && path ? path : null };
  }
  return { kind: "raw", text: event.text };
}

// The line above an approval's body.
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

// What copying an approval copies: the plan, prompt, command or script, else the event text.
export function approvalCopyText(event: EventRecord): string {
  const body = approvalBody(event);
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
      return event.text;
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

// A plan with no decision list offers every plan decision.
export function planApprovalActions(decisions: readonly PlanDecision[] | undefined): ApprovalAction<PlanDecision>[] {
  if (!decisions || decisions.length === 0) return [...PLAN_ACTIONS];
  const allowed = new Set(decisions);
  return PLAN_ACTIONS.filter((action) => allowed.has(action.id));
}
