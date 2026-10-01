"use client";

import { createContext, useContext, useState, type KeyboardEvent } from "react";

import { normalizeToolName } from "@/lib/events";
import { isModifiedEnterShortcut } from "@/lib/keyboard";
import type {
  EventRecord,
  PendingQuestionAvailability,
  PendingQuestionsSnapshot,
} from "@/lib/types";

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

// Resolution of an AskUserQuestion, derived once over the loaded events from
// durable evidence correlated by tool_use_id: an ask_user_question_answer user
// event, an explicit cancellation note, or a provider ending (paired result or
// closure note).
export type AskQuestionResolution =
  | { state: "pending" }
  | { state: "answered"; answerEvent: EventRecord }
  | { state: "cancelled"; cancelEvent: EventRecord }
  | { state: "closed_unanswered"; resultEvent: EventRecord };

// The selectable option list shared by the AskUserQuestion form and the inbox
// question block, so both render options identically.
export function AskQuestionOptions({
  options,
  selected,
  onToggle,
  disabled = false,
}: {
  options: AskQuestionOption[];
  selected: Set<string>;
  onToggle: (label: string) => void;
  disabled?: boolean;
}) {
  return (
    <ul className="ask-question-options">
      {options.map((option) => {
        const isSelected = selected.has(option.label);
        return (
          <li key={option.label}>
            <button
              type="button"
              className={`ask-option ${isSelected ? "selected" : ""}`}
              onClick={() => onToggle(option.label)}
              disabled={disabled}
              aria-pressed={isSelected}
            >
              <span className="ask-option-label">{option.label}</span>
              {option.description ? (
                <span className="ask-option-desc">{option.description}</span>
              ) : null}
            </button>
          </li>
        );
      })}
    </ul>
  );
}

export function parseAskUserQuestion(event: EventRecord): AskUserQuestion[] | null {
  const meta = event.metadata as Record<string, unknown> | undefined;
  const toolName =
    typeof meta?.tool_name === "string" ? normalizeToolName(meta.tool_name) : null;
  if (toolName !== "AskUserQuestion") {
    return null;
  }
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
    if (!options.length) continue;
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

// One in-progress answer: picked labels and notes per sub-question, plus which
// sub-question is showing.
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

export type AskOperation = "send" | "cancel";

// Session-scoped question state shared by the transcript cards and the
// pending-question dock, so both show one draft and one availability per
// request.
export interface AskQuestionController {
  // Null when the backend sends no pending-question snapshot; forms then stay
  // answerable while pending, without Cancel.
  snapshot: PendingQuestionsSnapshot | null;
  draft: (toolUseId: string) => AskDraft;
  updateDraft: (toolUseId: string, update: (draft: AskDraft) => AskDraft) => void;
  operation: (toolUseId: string) => AskOperation | null;
  answer: (
    toolUseId: string,
    text: string,
    answers: AskAnswerEntry[],
  ) => Promise<boolean>;
  cancel: (toolUseId: string) => Promise<boolean>;
  // Resolution from evidence across every loaded event, before transcript
  // filtering; null when this id has no card in the loaded window.
  resolution: (toolUseId: string) => AskQuestionResolution | null;
  canReattach: boolean;
  reattach: () => void;
}

export const AskQuestionContext = createContext<AskQuestionController | null>(null);

export function useAskQuestionController(): AskQuestionController | null {
  return useContext(AskQuestionContext);
}

// "gone": pending by loaded evidence but missing from a snapshot that already
// covers it — the provider ended it and its closure note is on the way.
export type AskFormState = PendingQuestionAvailability | "gone" | "resolved";

export function askFormState(
  controller: AskQuestionController | null,
  event: EventRecord,
  resolution: AskQuestionResolution,
): AskFormState {
  if (resolution.state !== "pending") return "resolved";
  const snapshot = controller?.snapshot;
  if (!snapshot) return "actionable";
  const toolUseId = askToolUseId(event);
  const entry = toolUseId
    ? snapshot.questions.find((question) => question.tool_use_id === toolUseId)
    : undefined;
  if (entry) return entry.availability;
  return event.sequence > snapshot.as_of_sequence ? "starting" : "gone";
}

function toggleLabel(
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
function serializeAnswers(
  questions: AskUserQuestion[],
  draft: AskDraft,
): { text: string; answers: AskAnswerEntry[] } | null {
  const segments: string[] = [];
  const structured: AskAnswerEntry[] = [];
  questions.forEach((entry, index) => {
    const selections = draft.picked[index] ?? [];
    const note = (draft.notes[index] ?? "").trim();
    if (!selections.length && !note) return;
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

// The answer form for one AskUserQuestion request: sub-question pager, options,
// custom notes, and Send / Clear / Cancel question. Draft, availability, and
// in-flight state come from the session's AskQuestionController so every
// surface showing this request agrees; without one it keeps a local draft and
// answers through `onAnswer`.
export function AskQuestionForm({
  event,
  questions,
  resolution,
  onAnswer,
}: {
  event: EventRecord;
  questions: AskUserQuestion[];
  resolution: AskQuestionResolution;
  onAnswer?: (
    text: string,
    toolUseId?: string,
    answers?: AskAnswerEntry[],
  ) => Promise<boolean> | void;
}) {
  const controller = useAskQuestionController();
  const toolUseId = askToolUseId(event);
  const [localDraft, setLocalDraft] = useState<AskDraft>(EMPTY_ASK_DRAFT);
  const [localSending, setLocalSending] = useState(false);
  const shared = Boolean(controller && toolUseId);
  const draft = shared ? controller!.draft(toolUseId!) : localDraft;
  const updateDraft = (update: (current: AskDraft) => AskDraft) => {
    if (shared) controller!.updateDraft(toolUseId!, update);
    else setLocalDraft(update);
  };
  const operation = shared ? controller!.operation(toolUseId!) : localSending ? "send" : null;
  const formState = askFormState(controller, event, resolution);
  const canAnswer = shared ? Boolean(toolUseId) : Boolean(onAnswer);
  const open = formState !== "resolved" && formState !== "gone";
  const actionable = formState === "actionable" && canAnswer;
  const editable = open && canAnswer;
  const busy = operation !== null;
  const canCancel = shared && Boolean(controller!.snapshot);

  const total = questions.length;
  const safeIndex = Math.min(draft.activeIndex, Math.max(0, total - 1));
  const entry = questions[safeIndex];
  const paginated = total > 1;
  const filled = (index: number) =>
    Boolean(draft.picked[index]?.length) || Boolean((draft.notes[index] ?? "").trim());
  const totalPicked = Object.values(draft.picked).reduce(
    (count, labels) => count + labels.length,
    0,
  );
  const totalNotes = Object.values(draft.notes).filter((value) => value.trim()).length;
  const canSubmit = actionable && !busy && (totalPicked > 0 || totalNotes > 0);

  async function submit() {
    if (!canSubmit) return;
    const payload = serializeAnswers(questions, draft);
    if (!payload) return;
    if (shared) {
      await controller!.answer(toolUseId!, payload.text, payload.answers);
      return;
    }
    setLocalSending(true);
    try {
      const ok = await onAnswer?.(payload.text, toolUseId ?? undefined, payload.answers);
      if (ok !== false) setLocalDraft(EMPTY_ASK_DRAFT);
    } finally {
      setLocalSending(false);
    }
  }

  function handleNoteKeyDown(keyEvent: KeyboardEvent<HTMLTextAreaElement>) {
    if (!isModifiedEnterShortcut(keyEvent)) return;
    keyEvent.preventDefault();
    void submit();
  }

  if (!entry) return null;
  const index = safeIndex;
  const selections = new Set(draft.picked[index] ?? []);
  const promptLabel = questions[0]?.question ?? "this question";

  return (
    <>
      {formState === "starting" ? (
        <p className="ask-question-status">Waiting for the agent to accept a reply…</p>
      ) : formState === "unavailable" ? (
        <p className="ask-question-status warn">
          <span>
            {controller?.canReattach
              ? "This session isn't running. Reattach it to answer or cancel."
              : "The agent can't take a reply to this question right now."}
          </span>
          {controller?.canReattach ? (
            <button
              type="button"
              className="secondary"
              onClick={() => controller.reattach()}
            >
              Reattach
            </button>
          ) : null}
        </p>
      ) : null}
      <div className="ask-question">
        {paginated ? (
          <div className="ask-question-pager">
            <span className="muted">
              Question {index + 1} of {total}
              {filled(index) ? " · answered" : ""}
            </span>
            <div className="ask-question-pager-dots" aria-hidden>
              {questions.map((_, dotIndex) => (
                <span
                  key={dotIndex}
                  className={`ask-question-pager-dot${
                    dotIndex === index ? " current" : ""
                  }${filled(dotIndex) ? " filled" : ""}`}
                />
              ))}
            </div>
          </div>
        ) : null}
        <div className="ask-question-head">
          {entry.header ? (
            <span className="badge neutral ask-question-chip">{entry.header}</span>
          ) : null}
          <p className="ask-question-text">{entry.question}</p>
          {entry.multiSelect ? <span className="meta">multi-select</span> : null}
        </div>
        <AskQuestionOptions
          options={entry.options}
          selected={selections}
          onToggle={(label) =>
            updateDraft((current) =>
              toggleLabel(current, index, label, entry.multiSelect ?? false),
            )
          }
          disabled={!actionable || busy}
        />
        {actionable ? (
          draft.notesOpen[index] ? (
            <div className="ask-question-note">
              <textarea
                className="ask-question-note-input"
                value={draft.notes[index] ?? ""}
                onChange={(changeEvent) => {
                  const value = changeEvent.target.value;
                  updateDraft((current) => ({
                    ...current,
                    notes: { ...current.notes, [index]: value },
                  }));
                }}
                onKeyDown={handleNoteKeyDown}
                placeholder="Type your own answer or add a note here…"
                rows={2}
                disabled={busy}
                aria-keyshortcuts="Meta+Enter Control+Enter"
              />
              <button
                type="button"
                className="link-button"
                onClick={() =>
                  updateDraft((current) => ({
                    ...current,
                    notesOpen: { ...current.notesOpen, [index]: false },
                  }))
                }
                disabled={busy}
              >
                Hide note
              </button>
            </div>
          ) : (
            <button
              type="button"
              className="link-button ask-question-note-toggle"
              onClick={() =>
                updateDraft((current) => ({
                  ...current,
                  notesOpen: { ...current.notesOpen, [index]: true },
                }))
              }
              disabled={busy}
            >
              + Other / Custom response
            </button>
          )
        ) : null}
        {paginated && open ? (
          <div className="ask-question-nav">
            <button
              type="button"
              className="secondary"
              disabled={busy || index === 0}
              onClick={() =>
                updateDraft((current) => ({
                  ...current,
                  activeIndex: Math.max(0, index - 1),
                }))
              }
            >
              ← Previous
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy || index === total - 1}
              onClick={() =>
                updateDraft((current) => ({
                  ...current,
                  activeIndex: Math.min(total - 1, index + 1),
                }))
              }
            >
              Next →
            </button>
          </div>
        ) : null}
      </div>
      {editable ? (
        <div className="action-row ask-question-actions">
          <button
            type="button"
            className="primary"
            disabled={!canSubmit}
            onClick={() => void submit()}
          >
            {operation === "send" ? "Sending…" : "Send answers"}
          </button>
          {totalPicked + totalNotes > 0 ? (
            <button
              type="button"
              className="secondary"
              disabled={busy || !actionable}
              onClick={() =>
                updateDraft((current) => ({
                  ...current,
                  picked: {},
                  notes: {},
                }))
              }
            >
              Clear
            </button>
          ) : null}
          {canCancel ? (
            <button
              type="button"
              className="link-button ask-question-cancel"
              disabled={!actionable || busy}
              onClick={() => void controller!.cancel(toolUseId!)}
              aria-label={`Cancel question: ${promptLabel}`}
            >
              {operation === "cancel" ? "Cancelling…" : "Cancel question"}
            </button>
          ) : null}
        </div>
      ) : null}
    </>
  );
}
