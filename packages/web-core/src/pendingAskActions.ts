import { answerAskQuestion, ApiError, approvePlan, approveSession, type AskAnswerPayload } from "./api";
import { FILE_EDIT_TOOLS, normalizeToolName, toolFilePath } from "./events";
import {
  approvalBodyForTool,
  holdsWholeRequest,
  runsOutsideSandbox,
  type ApprovalBody,
  type ShownRequest,
} from "./session/approvals";
import { toggleSelection } from "./session/askQuestion";
import type {
  PendingApprovalAsk,
  PendingAskOption,
  PendingAskPrompt,
  PendingPlanAsk,
  PendingQuestionAsk,
} from "./types";

// ─── Questions ───

// An option the agent recommends carries a trailing "(Recommended)" marker.
const RECOMMENDED_SUFFIX = /\s*\(recommended\)\s*$/i;

export function isRecommendedLabel(label: string): boolean {
  return RECOMMENDED_SUFFIX.test(label);
}

// The recommended flag carries the marker, so the label drops it.
export function stripRecommendedSuffix(label: string): string {
  return label.replace(RECOMMENDED_SUFFIX, "").trim() || label;
}

// Labels are the options' own text, "(Recommended)" included.
export interface PromptPick {
  labels: string[];
  other: string;
}

const EMPTY_PICK: PromptPick = { labels: [], other: "" };

export function emptyPicks(prompts: PendingAskPrompt[]): PromptPick[] {
  return prompts.map(() => EMPTY_PICK);
}

export function toggleOption(pick: PromptPick, label: string, multiSelect: boolean): PromptPick {
  return { ...pick, labels: toggleSelection(pick.labels, label, multiSelect) };
}

// A lone single-select question is answered by tapping an option, so a tap
// always sends it, a second tap after a failed send included.
export function tapOption(
  prompts: PendingAskPrompt[],
  picks: PromptPick[],
  index: number,
  label: string,
): { picks: PromptPick[]; send: boolean } {
  const prompt = prompts[index];
  const lone = prompts.length === 1 && !prompt.multi_select;
  const next = lone ? { labels: [label], other: "" } : toggleOption(picks[index], label, prompt.multi_select);
  return { picks: picks.map((pick, i) => (i === index ? next : pick)), send: lone };
}

// A lone prompt shows Send when an option tap does not send it.
export function showsSend(prompt: PendingAskPrompt, pick: PromptPick): boolean {
  return prompt.multi_select || pick.other.trim() !== "";
}

// A multi-select prompt shows every option; otherwise a cut list keeps the
// recommended answer in view.
export function visibleOptions(
  prompt: PendingAskPrompt,
  limit: number | undefined,
  expanded: boolean,
): { shown: PendingAskOption[]; limited: boolean } {
  const limited = limit !== undefined && !prompt.multi_select && !expanded && prompt.options.length > limit;
  if (!limited) return { shown: prompt.options, limited };
  const ordered = [...prompt.options].sort((a, b) => Number(b.recommended) - Number(a.recommended));
  return { shown: ordered.slice(0, limit), limited };
}

function pickValues(pick: PromptPick): string[] {
  const other = pick.other.trim();
  return other ? [...pick.labels, other] : pick.labels;
}

// Every prompt answered, by an option or by text.
export function isAnswerable(prompts: PendingAskPrompt[], picks: PromptPick[]): boolean {
  return prompts.length > 0 && prompts.every((_, index) => pickValues(picks[index] ?? EMPTY_PICK).length > 0);
}

export interface BuiltAnswer {
  // The `"question"="answer"` form Claude Code reads, joined across prompts.
  text: string;
  answers: AskAnswerPayload[];
}

// Selected labels and the Other text join with ", " into one answer per prompt.
export function buildAnswer(prompts: PendingAskPrompt[], picks: PromptPick[]): BuiltAnswer | null {
  if (!isAnswerable(prompts, picks)) return null;
  const answers = prompts.map((prompt, index) => ({
    question: prompt.question,
    answer: pickValues(picks[index] ?? EMPTY_PICK).join(", "),
  }));
  return {
    text: answers.map((entry) => `"${entry.question}"="${entry.answer}"`).join(", "),
    answers,
  };
}

// ─── Approvals and plans ───

export type DecisionKind = "approve" | "session" | "decline";

export interface DecisionAction {
  // The id sent to the approve endpoint.
  decision: string;
  label: string;
  kind: DecisionKind;
}

// "acceptForSession", "allow_for_session", "approve-for-this-session": matched by
// shape so no agent is named.
const SESSION_ALLOW = /^(accept|allow|approve)(for)?(this)?session$/;

export function sessionScopedAllow(decisions: readonly string[]): string | undefined {
  return decisions.find((decision) => SESSION_ALLOW.test(decision.toLowerCase().replace(/[^a-z]/g, "")));
}

// Every agent maps accept and decline to allow and deny; the session-scoped
// allow shows whenever the agent lists one.
function decisionActions(decisions: readonly string[]): DecisionAction[] {
  const forSession = sessionScopedAllow(decisions);
  return [
    { decision: "accept", label: "Approve", kind: "approve" },
    ...(forSession ? [{ decision: forSession, label: "For this session", kind: "session" as const }] : []),
    { decision: "decline", label: "Decline", kind: "decline" },
  ];
}

export type ApprovalPreview = { kind: "command"; text: string } | { kind: "path"; path: string } | null;

// What a tool approval is about: the shell command, or the file being edited.
function approvalPreview(approval: PendingApprovalAsk["approval"]): ApprovalPreview {
  const tool = normalizeToolName(approval.tool_name);
  const input = approval.tool_input;
  if (tool === "Bash" && typeof input?.command === "string" && input.command) {
    return { kind: "command", text: input.command };
  }
  const path = tool && FILE_EDIT_TOOLS.has(tool) ? toolFilePath(input) : null;
  return path ? { kind: "path", path } : null;
}

// The first lines of a plan with the markdown marks off, for a card.
export function planPreview(text: string, maxLines = 3): string {
  return text
    .split("\n")
    .map((line) => line.replace(/^[#>\s]+/, "").replace(/[*`]/g, "").trim())
    .filter(Boolean)
    .slice(0, maxLines)
    .join("\n");
}

export interface DecisionView {
  actions: DecisionAction[];
  supportsNote: boolean;
  preview: ApprovalPreview;
  // The full plan, for a plan approval.
  planBody: string | null;
}

export function decisionView(ask: PendingApprovalAsk | PendingPlanAsk): DecisionView {
  if (ask.kind === "approval") {
    const { approval } = ask;
    return {
      actions: decisionActions(approval.decisions),
      supportsNote: approval.supports_note,
      preview: approvalPreview(approval),
      planBody: null,
    };
  }
  return {
    actions: decisionActions(ask.plan.decisions),
    supportsNote: ask.plan.supports_note,
    preview: null,
    planBody: ask.plan.text,
  };
}

// What a row or card lists whole beside its actions: a command or question of a few
// short lines.
const LISTED_MAX_LENGTH = 160;
const LISTED_MAX_LINES = 3;

export const fitsListing = (text: string) =>
  text.length <= LISTED_MAX_LENGTH && text.split("\n").length <= LISTED_MAX_LINES;

// The ask holds a shell command or an edited file's path, uncut by the server.
function carriesRequest(ask: PendingApprovalAsk | PendingPlanAsk): boolean {
  return ask.kind === "approval" && !ask.approval.tool_input_truncated && decisionView(ask).preview !== null;
}

// A decision is live only while the whole request is on screen. A row or card can show
// a command whole; an edit's request is its change, and a command that leaves the sandbox
// says so, both only in a detail.
export function decidesInline(ask: PendingApprovalAsk | PendingPlanAsk): boolean {
  const { preview } = decisionView(ask);
  if (preview?.kind !== "command" || !carriesRequest(ask) || !fitsListing(preview.text)) return false;
  return ask.kind === "approval" && !runsOutsideSandbox(ask.approval.tool_input);
}

// What a read of the transcript turned up for a pending approval.
export type ApprovalRead = ShownRequest | "missing" | "failed";

export type ApprovalRequest = { status: "loading" } | { status: "failed" } | ({ status: "ready" } & ShownRequest);

// A plan travels in its ask. A tool approval shows what the transcript holds; when
// the transcript has no such event, the ask's own input is used only where it is
// the whole request.
export function approvalRequest(ask: PendingApprovalAsk | PendingPlanAsk, read: ApprovalRead | null): ApprovalRequest {
  if (ask.kind === "plan_approval") {
    const body: ApprovalBody = { kind: "plan", plan: ask.plan.text };
    return { status: "ready", body, whole: holdsWholeRequest(body, null) };
  }
  if (read === null) return { status: "loading" };
  if (typeof read !== "string") return { status: "ready", ...read };
  if (read === "failed" || !carriesRequest(ask)) return { status: "failed" };
  const { tool_name: tool, tool_input: input } = ask.approval;
  const name = normalizeToolName(tool);
  const body = approvalBodyForTool(name, input, ask.body ?? ask.title);
  return { status: "ready", body, whole: holdsWholeRequest(body, input, name) };
}

// ─── Responding ───

export const NOT_READY_MESSAGE = "The agent isn't ready for a reply yet. Try again in a moment.";

// A reply sent right after a question appears can hit a session that is still
// opening its dialog: 409 "the session is waiting on a dialog" or "the agent is
// not ready for a reply". Other conflicts (a session that is not running, an
// operation already in progress) are real answers and are not retried.
const NOT_READY_DETAIL = /not ready|waiting on a dialog/i;

export function isAgentNotReady(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409 && NOT_READY_DETAIL.test(error.message);
}

export interface RetryOptions {
  attempts?: number;
  delayMs?: number;
  sleep?: (ms: number) => Promise<void>;
  // Called before each wait, so the UI can say it is retrying.
  onRetry?: () => void;
}

const defaultSleep = (ms: number) => new Promise<void>((resolve) => setTimeout(resolve, ms));

// Retries while the agent is not ready, about 3 s in all, then rethrows.
export async function retryWhileNotReady<T>(run: () => Promise<T>, options: RetryOptions = {}): Promise<T> {
  const { attempts = 5, delayMs = 700, sleep = defaultSleep, onRetry } = options;
  for (let attempt = 1; ; attempt += 1) {
    try {
      return await run();
    } catch (error) {
      if (!isAgentNotReady(error) || attempt >= attempts) throw error;
      onRetry?.();
      await sleep(delayMs);
    }
  }
}

export function answerQuestion(
  host: string,
  token: string,
  ask: PendingQuestionAsk,
  picks: PromptPick[],
  retry: RetryOptions = {},
): Promise<void> {
  const built = buildAnswer(ask.question.questions, picks);
  if (!built) return Promise.reject(new Error("nothing to send"));
  return retryWhileNotReady(
    () => answerAskQuestion(host, token, ask.session_id, built.text, ask.question.tool_use_id, built.answers),
    retry,
  );
}

// A tool approval goes to approve; a plan to approve or approve-plan as its
// `resolve_via` names.
export function decideAsk(
  host: string,
  token: string,
  ask: PendingApprovalAsk | PendingPlanAsk,
  decision: string,
  note: string | undefined,
  retry: RetryOptions = {},
): Promise<void> {
  const text = note?.trim() || undefined;
  const send = async () => {
    if (ask.kind === "approval") {
      await approveSession(host, token, ask.session_id, decision, text, ask.approval.approval_id);
    } else if (ask.plan.resolve_via === "approve_plan") {
      // approve-plan has its own fixed vocabulary; the session-scoped allow is its acceptForSession.
      const planDecision = decision === "accept" || decision === "decline" ? decision : "acceptForSession";
      await approvePlan(host, token, ask.session_id, ask.plan.plan_id, planDecision, text);
    } else {
      await approveSession(host, token, ask.session_id, decision, text, ask.plan.plan_id);
    }
  };
  return retryWhileNotReady(send, retry);
}
