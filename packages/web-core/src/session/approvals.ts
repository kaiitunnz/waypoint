import { supportsPlanApproval, type BackendCatalog } from "../backends";
import {
  asRecord,
  FILE_EDIT_TOOLS,
  parseEvent,
  planForEvent,
  readToolName,
  toolFilePath,
  type EventDiffPreview,
  type PlanDecision,
  type PlanViewModel,
} from "../events";
import type { EventRecord, SessionRecord } from "../types";
import { resolveFromCwd, splitPath } from "../workspacePaths";
import { hunksAddUp } from "../highlight";
import { toolInputForEvent } from "./toolPresentation";
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
  | { kind: "command"; description: string; command: string; outsideSandbox?: boolean }
  | { kind: "workflow"; script: string }
  | { kind: "edit"; tool: string; path: string | null }
  | { kind: "raw"; text: string };

const WORKFLOW_TOOLS = new Set(["Workflow", "RunWorkflow"]);
// Tools that ask to run a text of their own (a plan, prompt, command or script): without
// it, what is asked is not on screen.
const CONTENT_TOOLS = new Set(["ExitPlanMode", "Task", "Agent", "Bash", ...WORKFLOW_TOOLS]);

const text = (value: unknown): string => (typeof value === "string" ? value : "");

export function approvalBody(event: EventRecord): ApprovalBody {
  const preview = parseEvent(event).diffPreview;
  if (preview) return { kind: "diff", prompt: event.text, preview };
  return approvalBodyForTool(readToolName(event), asRecord(event.metadata?.tool_input), event.text);
}

// A tool with no body of its own shows its whole input: its request text may be only
// the start. A request that carries a command beside its title shows the command.
export function wholeApprovalBody(event: EventRecord): ApprovalBody {
  const body = approvalBody(event);
  if (body.kind !== "raw") return body;
  const input = asRecord(event.metadata?.tool_input);
  if (input) {
    return Object.keys(input).length === 0 ? body : { kind: "raw", text: JSON.stringify(input, null, 2) };
  }
  const command = asRecord(event.metadata?.request)?.command;
  return typeof command === "string" && command ? { kind: "command", description: "", command } : body;
}

// Lines show only under a numbered hunk header, so a diff without one shows nothing, and
// one whose hunks do not add up shows something other than the change.
const hasWholeDiff = (file: EventDiffPreview["files"][number]) =>
  !file.truncated && !file.binary && !file.unavailableReason && /^@@ -\d/m.test(file.diff) && hunksAddUp(file.diff);

const callId = (event: EventRecord) => event.metadata?.tool_use_id ?? event.metadata?.item_id;

const fileName = (path: string | null) => (path === null ? null : splitPath(path).base);

// A pending Write's content, only when it is certain: from the request itself, else from
// the transcript's one unanswered Write since the human last spoke that has this file
// name, if it is of this very path. A request may name the file without its folder, so a
// second file of that name leaves it unknown. An empty write has nothing to show.
function pendingWrite(
  request: EventRecord,
  events: readonly EventRecord[],
  cwd: string,
): { path: string; content: string } | null {
  const input = asRecord(request.metadata?.tool_input);
  const path = toolFilePath(input);
  if (readToolName(request) !== "Write" || !path) return null;
  if (typeof input?.content === "string") return input.content ? { path, content: input.content } : null;
  const asked = resolveFromCwd(path, cwd);
  if (!asked) return null;
  const before = events.filter((event) => event.sequence < request.sequence);
  const turn = before.slice(before.map((event) => event.kind).lastIndexOf("user_input") + 1);
  const answered = new Set(turn.filter((event) => event.kind === "tool_result").map(callId));
  const calls = turn.filter(
    (event) =>
      event.kind === "tool_call" &&
      readToolName(event) === "Write" &&
      !answered.has(callId(event)) &&
      fileName(toolFilePath(toolInputForEvent(event))) === fileName(asked),
  );
  if (calls.length !== 1 || toolFilePath(toolInputForEvent(calls[0])) !== asked) return null;
  const content = toolInputForEvent(calls[0])?.content;
  return typeof content === "string" && content ? { path: asked, content } : null;
}

// A write replaces the file, so its whole content is the change.
function writeDiff(path: string, content: string): EventDiffPreview {
  const lines = content.replace(/\n$/, "").split("\n");
  const diff = [`@@ -0,0 +1,${lines.length} @@`, ...lines.map((line) => `+${line}`), ""].join("\n");
  const file = { path, changeType: "unknown" as const, diff, additions: lines.length, deletions: 0 };
  return {
    schemaVersion: 1,
    phase: "proposed",
    files: [{ ...file, truncated: false, binary: false }],
    totalAdditions: lines.length,
    totalDeletions: 0,
    truncated: false,
  };
}

// `whole`: the body is all of what is asked, so it can be decided on.
export interface ShownRequest {
  body: ApprovalBody;
  whole: boolean;
}

// A pending request as its screen shows it. `events` are those up to the request, and
// `cwd` the session's.
export function shownRequest(request: EventRecord, events: readonly EventRecord[] = [], cwd = ""): ShownRequest {
  const write = parseEvent(request).diffPreview ? null : pendingWrite(request, events, cwd);
  const body: ApprovalBody = write
    ? { kind: "diff", prompt: request.text, preview: writeDiff(write.path, write.content) }
    : wholeApprovalBody(request);
  return { body, whole: holdsWholeRequest(body, asRecord(request.metadata?.tool_input), readToolName(request)) };
}

// Whether a body holds the whole of what is asked, `input` and `tool` being the call's
// own. Nobody decides on less: a plan, prompt or command with no text, an edit without
// its content, a diff that is cut or lists a file without it, or a request known only by
// its title or method.
export function holdsWholeRequest(
  body: ApprovalBody,
  input: Record<string, unknown> | null,
  tool: string | null = null,
): boolean {
  switch (body.kind) {
    case "plan":
      return body.plan.trim() !== "";
    case "task":
      return body.prompt.trim() !== "";
    case "command":
      return body.command.trim() !== "";
    case "workflow":
      return body.script.trim() !== "";
    case "diff":
      return !body.preview.truncated && body.preview.files.every(hasWholeDiff);
    // The path alone: the change is what is asked.
    case "edit":
      return false;
    case "raw": {
      if (!input || (tool && CONTENT_TOOLS.has(tool))) return false;
      // A call whose every argument came through empty shows nothing of itself.
      const values = Object.values(input);
      return values.length === 0 || values.some((value) => value !== "");
    }
  }
}

export const runsOutsideSandbox = (input: Record<string, unknown> | null) => input?.dangerouslyDisableSandbox === true;

// `tool` is a normalized tool name; `raw` is the text where no kind matches.
export function approvalBodyForTool(
  tool: string | null,
  input: Record<string, unknown> | null,
  raw: string,
): ApprovalBody {
  if (tool === "ExitPlanMode" && typeof input?.plan === "string") return { kind: "plan", plan: input.plan };
  if ((tool === "Task" || tool === "Agent") && typeof input?.prompt === "string") {
    return { kind: "task", prompt: input.prompt, description: text(input.description), subagent: text(input.subagent_type) };
  }
  if (tool === "Bash" && typeof input?.command === "string") {
    const command = { kind: "command" as const, description: text(input.description), command: input.command };
    return runsOutsideSandbox(input) ? { ...command, outsideSandbox: true } : command;
  }
  if (tool && WORKFLOW_TOOLS.has(tool) && typeof input?.script === "string") {
    return { kind: "workflow", script: input.script };
  }
  if (tool && FILE_EDIT_TOOLS.has(tool)) return { kind: "edit", tool, path: toolFilePath(input) };
  return { kind: "raw", text: raw };
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
    case "raw":
      return body.text;
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
