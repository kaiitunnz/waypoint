import { answerAskQuestion, ApiError, approvePlan, approveSession, type AskAnswerPayload } from "./api";
import { normalizeToolName } from "./events";
import type {
  PendingAsk,
  PendingAskApproval,
  PendingAskPlan,
  PendingAskPrompt,
} from "./types";

// ─── Questions ───

// The label an option shows: the agent's "(Recommended)" marker is carried by
// the `recommended` flag instead.
export function stripRecommendedSuffix(label: string): string {
  return label.replace(/\s*\(recommended\)\s*$/i, "").trim() || label;
}

// What the human picked for one prompt: option labels (verbatim) and an
// optional free-text "Other" answer.
export interface PromptPick {
  labels: string[];
  other: string;
}

export const EMPTY_PICK: PromptPick = { labels: [], other: "" };

export function emptyPicks(prompts: PendingAskPrompt[]): PromptPick[] {
  return prompts.map(() => EMPTY_PICK);
}

// A single-select prompt holds one label (tapping it again clears it); a
// multi-select prompt toggles each label.
export function toggleOption(pick: PromptPick, label: string, multiSelect: boolean): PromptPick {
  const has = pick.labels.includes(label);
  if (multiSelect) {
    return { ...pick, labels: has ? pick.labels.filter((item) => item !== label) : [...pick.labels, label] };
  }
  return { ...pick, labels: has ? [] : [label] };
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

export type AskDecision = "accept" | "acceptForSession" | "decline";

export interface DecisionAction {
  decision: AskDecision;
  label: string;
  tone: "primary" | "quiet" | "danger";
}

// Approve and Decline always show (every agent maps them to allow and deny); an
// escalation only when the agent lists it.
function decisionActions(decisions: readonly string[]): DecisionAction[] {
  return [
    { decision: "accept", label: "Approve", tone: "primary" },
    ...(decisions.includes("acceptForSession")
      ? [{ decision: "acceptForSession" as const, label: "For this session", tone: "quiet" as const }]
      : []),
    { decision: "decline", label: "Decline", tone: "danger" },
  ];
}

export const approvalActions = (approval: PendingAskApproval) => decisionActions(approval.decisions);
export const planActions = (plan: PendingAskPlan) => decisionActions(plan.decisions);

export type ApprovalPreview =
  | { kind: "command"; text: string }
  | { kind: "path"; tool: string; path: string }
  | { kind: "none" };

const FILE_EDIT_TOOLS = new Set(["Edit", "MultiEdit", "Write", "NotebookEdit"]);

// What an approval is about, from its tool input: the shell command, or the file
// being edited.
export function approvalPreview(approval: PendingAskApproval): ApprovalPreview {
  const tool = normalizeToolName(approval.tool_name);
  const input = approval.tool_input;
  if (tool === "Bash" && typeof input?.command === "string" && input.command) {
    return { kind: "command", text: input.command };
  }
  if (tool && FILE_EDIT_TOOLS.has(tool)) {
    const path = input?.file_path ?? input?.path ?? input?.notebook_path;
    if (typeof path === "string" && path) return { kind: "path", tool, path };
  }
  return { kind: "none" };
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

// ─── Responding ───

export const NOT_READY_MESSAGE = "The agent isn't ready for a reply yet. Try again in a moment.";

// A reply sent right after a question appears can hit a session that is still
// opening its dialog: 409 "the session is waiting on a dialog".
export function isAgentNotReady(error: unknown): boolean {
  return error instanceof ApiError && error.status === 409;
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

export type AskResponse =
  | { kind: "answer"; picks: PromptPick[] }
  | { kind: "decision"; decision: AskDecision; note?: string };

// Sends a response through the ask's own per-session endpoint: an answer to
// answer-question, an approval to approve, a plan to approve or approve-plan as
// its `resolve_via` names.
export function respondToAsk(
  host: string,
  token: string,
  ask: PendingAsk,
  response: AskResponse,
  retry: RetryOptions = {},
): Promise<void> {
  const send = async () => {
    if (response.kind === "answer") {
      if (ask.kind !== "question") throw new Error("not a question");
      const built = buildAnswer(ask.question.questions, response.picks);
      if (!built) throw new Error("nothing to send");
      await answerAskQuestion(host, token, ask.session_id, built.text, ask.question.tool_use_id, built.answers);
      return;
    }
    const note = response.note?.trim() || undefined;
    if (ask.kind === "approval") {
      await approveSession(host, token, ask.session_id, response.decision, note, ask.approval.approval_id);
    } else if (ask.kind === "plan_approval" && ask.plan.resolve_via === "approve_plan") {
      await approvePlan(host, token, ask.session_id, ask.plan.plan_id, response.decision, note);
    } else if (ask.kind === "plan_approval") {
      await approveSession(host, token, ask.session_id, response.decision, note, ask.plan.plan_id);
    } else {
      throw new Error("not an approval");
    }
  };
  return retryWhileNotReady(send, retry);
}
