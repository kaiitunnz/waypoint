import { answerAskQuestion, ApiError, approvePlan, approveSession, fetchEvents, type AskAnswerPayload } from "./api";
import { FILE_EDIT_TOOLS, normalizeToolName, toolFilePath } from "./events";
import { toggleSelection } from "./session/askQuestion";
import { shownRequest, type AskSession, type ShownRequest } from "./session/wholeRequest";
import type {
  EventRecord,
  PendingApprovalAsk,
  PendingAsk,
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

// A queue decides one request at a time: a wider grant's scope is not on screen.
export type Decision = "accept" | "decline";

export interface DecisionAction {
  // The id sent to the approve endpoint, which every agent takes.
  decision: Decision;
  label: string;
}

const DECISION_ACTIONS: readonly DecisionAction[] = [
  { decision: "accept", label: "Approve" },
  { decision: "decline", label: "Decline" },
];

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
  actions: readonly DecisionAction[];
  supportsNote: boolean;
  preview: ApprovalPreview;
  // The full plan, for a plan approval.
  planBody: string | null;
}

export function decisionView(ask: PendingApprovalAsk | PendingPlanAsk): DecisionView {
  if (ask.kind === "approval") {
    const { approval } = ask;
    return {
      actions: DECISION_ACTIONS,
      supportsNote: approval.supports_note,
      preview: approvalPreview(approval),
      planBody: null,
    };
  }
  return {
    actions: DECISION_ACTIONS,
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

// All a shell call may carry for a row to decide it.
const INLINE_INPUT = new Set(["command", "description", "timeout", "run_in_background"]);

// The command a row or card takes a decision on, or null: a shell call that carries
// nothing else of consequence and whose command lists whole. `input` is the call's own,
// uncut, or null where that is not known.
export function inlineCommand(tool: string | null, input: Record<string, unknown> | null): string | null {
  if (normalizeToolName(tool) !== "Bash" || !input) return null;
  if (!Object.keys(input).every((key) => INLINE_INPUT.has(key))) return null;
  const { command } = input;
  return typeof command === "string" && command.trim() !== "" && fitsListing(command) ? command : null;
}

// What a row or card knows of the call behind an ask.
export interface AskCall {
  input: Record<string, unknown> | null;
  // The transcript is being read for it.
  reading: boolean;
}

export const NO_CALL: AskCall = { input: null, reading: false };

// What the ask alone says of its call, or null where the transcript has to say: a shell
// command read off the screen that may be short enough to list.
export function askedCall(ask: PendingAsk, fromScreen: boolean): AskCall | null {
  if (ask.kind !== "approval") return NO_CALL;
  const { tool_name: tool, tool_input: input, tool_input_truncated: cut } = ask.approval;
  if (!fromScreen) return { input: cut ? null : input, reading: false };
  if (normalizeToolName(tool) !== "Bash" || typeof input?.command !== "string") return NO_CALL;
  // The screen's rows and padding only ever add to the command.
  return input.command.replace(/\s+/g, " ").trim().length <= LISTED_MAX_LENGTH ? null : NO_CALL;
}

// The transcript around a pending request, up to the present.
export type TranscriptRead = { request: EventRecord; events: EventRecord[] } | "missing" | "failed";

// How far back from a request the call behind it is looked for.
const TRANSCRIPT_MESSAGES = 20;

// The page ending at the request, since a pending ask can be many messages old, then
// everything recorded since.
export async function readTranscript(
  host: string,
  token: string,
  sessionId: string,
  requestId: string,
  sequence: number,
): Promise<TranscriptRead> {
  const page = await fetchEvents(host, token, sessionId, {
    beforeSequence: sequence + 1,
    messages: TRANSCRIPT_MESSAGES,
  });
  const request = page.events.find(
    (event) => event.kind === "approval_request" && event.metadata?.approval_id === requestId,
  );
  if (request?.id === undefined) return "missing";
  const since = await fetchEvents(host, token, sessionId, { afterId: request.id });
  return { request, events: [...page.events, ...since.events] };
}

export type ApprovalRequest = { status: "loading" } | { status: "failed" } | ({ status: "ready" } & ShownRequest);

const planView = (plan: string) => ({ body: { kind: "plan" as const, plan }, whole: plan.trim() !== "" });

// A request is shown from its transcript. A plan's text travels in its ask, and one
// from Waypoint's own plan mode has no call behind it, so the text is all of it.
export function approvalRequest(
  ask: PendingApprovalAsk | PendingPlanAsk,
  read: TranscriptRead | null,
  session: Pick<AskSession, "cwd" | "fromScreen">,
): ApprovalRequest {
  if (ask.kind === "plan_approval" && ask.plan.resolve_via === "approve_plan") {
    return { status: "ready", ...planView(ask.plan.text), fields: null, warnings: [], input: null };
  }
  if (read === null) return { status: "loading" };
  if (typeof read === "string") return { status: "failed" };
  const shown = shownRequest(read.request, read.events, session);
  return { status: "ready", ...shown, ...(ask.kind === "plan_approval" ? planView(ask.plan.text) : null) };
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
  decision: Decision,
  note: string | undefined,
  retry: RetryOptions = {},
): Promise<void> {
  const text = note?.trim() || undefined;
  const send = async () => {
    if (ask.kind === "approval") {
      await approveSession(host, token, ask.session_id, decision, text, ask.approval.approval_id);
    } else if (ask.plan.resolve_via === "approve_plan") {
      await approvePlan(host, token, ask.session_id, ask.plan.plan_id, decision, text);
    } else {
      await approveSession(host, token, ask.session_id, decision, text, ask.plan.plan_id);
    }
  };
  return retryWhileNotReady(send, retry);
}
