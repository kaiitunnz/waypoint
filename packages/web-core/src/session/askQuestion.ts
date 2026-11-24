import { readToolName } from "../events";
import type {
  EventRecord,
  PendingQuestionAvailability,
  PendingQuestionsSnapshot,
} from "../types";

export interface AskQuestionOption {
  label: string;
  description?: string;
}

export interface AskUserQuestion {
  question: string;
  header?: string;
  options: AskQuestionOption[];
  multiSelect?: boolean;
}

export interface AskAnswerEntry {
  question: string;
  answer: string | null;
  notes?: string;
}

export type AskQuestionResolution =
  | { state: "pending" }
  | { state: "answered"; answerEvent: EventRecord }
  | { state: "cancelled"; cancelEvent: EventRecord }
  | { state: "closed_unanswered"; resultEvent: EventRecord };

export function parseAskUserQuestion(event: EventRecord): AskUserQuestion[] | null {
  if (readToolName(event) !== "AskUserQuestion") {
    return null;
  }
  const meta = event.metadata as Record<string, unknown> | undefined;
  const payload = meta?.payload as { input?: unknown } | undefined;
  const input = payload?.input as { questions?: unknown } | undefined;
  const raw = input?.questions;
  if (!Array.isArray(raw) || raw.length === 0) {
    return null;
  }
  const parsed: AskUserQuestion[] = [];
  for (const entry of raw) {
    if (!entry || typeof entry !== "object") continue;
    const q = entry as Record<string, unknown>;
    if (typeof q.question !== "string") continue;
    const optionsRaw = Array.isArray(q.options) ? q.options : [];
    const options: AskQuestionOption[] = [];
    for (const opt of optionsRaw) {
      if (!opt || typeof opt !== "object") continue;
      const o = opt as Record<string, unknown>;
      if (typeof o.label !== "string") continue;
      options.push({
        label: o.label,
        description: typeof o.description === "string" ? o.description : undefined,
      });
    }
    parsed.push({
      question: q.question,
      header: typeof q.header === "string" ? q.header : undefined,
      options,
      multiSelect: q.multiSelect === true,
    });
  }
  return parsed.length ? parsed : null;
}

export function askToolUseId(event: EventRecord): string | null {
  const id = event.metadata?.tool_use_id;
  return typeof id === "string" && id ? id : null;
}

export interface AskDraft {
  picked: Record<number, string[]>;
  notes: Record<number, string>;
  notesOpen: Record<number, boolean>;
  activeIndex: number;
}

export const EMPTY_ASK_DRAFT: AskDraft = {
  picked: {},
  notes: {},
  notesOpen: {},
  activeIndex: 0,
};

// Durable evidence that resolved an AskUserQuestion, keyed by tool_use_id.
// Indexed over every loaded event before transcript filtering so hiding a
// system note never reopens its card.
export interface AskQuestionEvidence {
  answers: Map<string, EventRecord>;
  cancels: Map<string, EventRecord>;
  endings: Map<string, EventRecord>;
}

export function indexAskQuestionEvidence(events: EventRecord[]): AskQuestionEvidence {
  const evidence: AskQuestionEvidence = {
    answers: new Map(),
    cancels: new Map(),
    endings: new Map(),
  };
  const keep = (index: Map<string, EventRecord>, id: string, event: EventRecord) => {
    const existing = index.get(id);
    if (!existing || event.sequence < existing.sequence) index.set(id, event);
  };
  for (const event of events) {
    const toolUseId = askToolUseId(event);
    if (!toolUseId) continue;
    const metadata = event.metadata ?? {};
    if (event.kind === "user_input" && metadata.kind === "ask_user_question_answer") {
      keep(evidence.answers, toolUseId, event);
    } else if (event.kind === "system_note") {
      if (metadata.kind === "ask_user_question_cancelled") {
        keep(evidence.cancels, toolUseId, event);
      } else if (metadata.kind === "ask_user_question_closed") {
        keep(evidence.endings, toolUseId, event);
      }
    } else if (event.kind === "tool_result") {
      keep(evidence.endings, toolUseId, event);
    }
  }
  return evidence;
}

// A correlated answer wins, then an explicit cancellation, then a provider
// ending; otherwise the question is still pending.
export function resolveAskQuestion(
  toolUseId: string,
  evidence: AskQuestionEvidence,
): AskQuestionResolution {
  const answerEvent = evidence.answers.get(toolUseId);
  if (answerEvent) return { state: "answered", answerEvent };
  const cancelEvent = evidence.cancels.get(toolUseId);
  if (cancelEvent) return { state: "cancelled", cancelEvent };
  const resultEvent = evidence.endings.get(toolUseId);
  if (resultEvent) return { state: "closed_unanswered", resultEvent };
  return { state: "pending" };
}

// "loading": no snapshot yet. "gone": pending by loaded evidence but missing
// from a snapshot that already covers it — the provider ended it and its
// closure note is on the way.
export type AskFormState = PendingQuestionAvailability | "loading" | "gone" | "resolved";

export function askFormState(
  snapshot: PendingQuestionsSnapshot | null,
  event: EventRecord,
  resolution: AskQuestionResolution,
): AskFormState {
  if (resolution.state !== "pending") return "resolved";
  if (!snapshot) return "loading";
  const toolUseId = askToolUseId(event);
  const entry = toolUseId
    ? snapshot.questions.find((question) => question.tool_use_id === toolUseId)
    : undefined;
  if (entry) return entry.availability;
  return event.sequence > snapshot.as_of_sequence ? "starting" : "gone";
}

export function toggleLabel(
  draft: AskDraft,
  questionIndex: number,
  label: string,
  multiSelect: boolean,
): AskDraft {
  const current = draft.picked[questionIndex] ?? [];
  let next: string[];
  if (multiSelect) {
    next = current.includes(label)
      ? current.filter((item) => item !== label)
      : [...current, label];
  } else {
    next = current.length === 1 && current[0] === label ? [] : [label];
  }
  return { ...draft, picked: { ...draft.picked, [questionIndex]: next } };
}

// Match the Claude binary's mapToolResultToToolResultBlockParam shape so the
// model parses the answer the same way native Claude Code does:
// `"<question>"="<answer>" user notes: <notes>`, joined by `, ` across
// questions. Questions with neither an answer nor notes are skipped.
export function serializeAnswers(
  questions: AskUserQuestion[],
  draft: AskDraft,
): { text: string; answers: AskAnswerEntry[] } | null {
  const segments: string[] = [];
  const structured: AskAnswerEntry[] = [];
  questions.forEach((entry, index) => {
    const selections = draft.picked[index] ?? [];
    const note = (draft.notes[index] ?? "").trim();
    if (!selections.length && !note) return;
    if (!entry.options.length) {
      segments.push(`"${entry.question}"="${note}"`);
      structured.push({ question: entry.question, answer: note });
      return;
    }
    const parts: string[] = [];
    let answerValue: string | null = null;
    if (selections.length) {
      answerValue = selections.join(", ");
      parts.push(`"${entry.question}"="${answerValue}"`);
    } else {
      parts.push(`"${entry.question}"=(no option selected)`);
    }
    if (note) {
      parts.push(`user notes: ${note}`);
    }
    segments.push(parts.join(" "));
    structured.push({
      question: entry.question,
      answer: answerValue,
      notes: note || undefined,
    });
  });
  return segments.length ? { text: segments.join(", "), answers: structured } : null;
}

// Snapshots order by (as_of_sequence, revision). A socket's first frame
// replaces the list outright: the server's revision counter restarts with
// the backend, so a reconnect must not be compared against the old one.
export function mergePendingQuestionSnapshot(
  current: PendingQuestionsSnapshot | null,
  next: PendingQuestionsSnapshot,
  replace: boolean,
): PendingQuestionsSnapshot {
  if (replace || current === null) return next;
  if (next.as_of_sequence !== current.as_of_sequence) {
    return next.as_of_sequence > current.as_of_sequence ? next : current;
  }
  return next.revision >= current.revision ? next : current;
}
