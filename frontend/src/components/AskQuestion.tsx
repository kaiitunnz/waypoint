"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
} from "react";

import { answerAskQuestion, cancelAskQuestion } from "@/lib/api";
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

export type AskOperation = "send" | "cancel";

// Session-scoped question state shared by the transcript cards and the
// pending-question dock, so both show one draft and one availability per
// request.
export interface AskQuestionController {
  // Null until the session's first snapshot arrives.
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
  resolution: (toolUseId: string) => AskQuestionResolution;
  canReattach: boolean;
  reattach: () => void;
}

export const AskQuestionContext = createContext<AskQuestionController | null>(null);

export function useAskQuestionController(): AskQuestionController {
  const controller = useContext(AskQuestionContext);
  if (!controller) {
    throw new Error("AskQuestion components need an AskQuestionContext provider");
  }
  return controller;
}

// Snapshots order by (as_of_sequence, revision). A socket's first frame
// replaces the list outright: the server's revision counter restarts with
// the backend, so a reconnect must not be compared against the old one.
export function usePendingQuestionSnapshot(sessionId: string) {
  const [snapshot, setSnapshot] = useState<PendingQuestionsSnapshot | null>(null);
  useEffect(() => {
    setSnapshot(null);
  }, [sessionId]);
  const applySnapshot = useCallback(
    (next: PendingQuestionsSnapshot, replace: boolean) => {
      setSnapshot((current) => {
        if (replace || current === null) return next;
        if (next.as_of_sequence !== current.as_of_sequence) {
          return next.as_of_sequence > current.as_of_sequence ? next : current;
        }
        return next.revision >= current.revision ? next : current;
      });
    },
    [],
  );
  return { snapshot, applySnapshot };
}

// Durable evidence that resolved an AskUserQuestion, keyed by tool_use_id.
// Indexed over every loaded event before transcript filtering so hiding a
// system note never reopens its card.
interface AskQuestionEvidence {
  answers: Map<string, EventRecord>;
  cancels: Map<string, EventRecord>;
  endings: Map<string, EventRecord>;
}

function indexAskQuestionEvidence(events: EventRecord[]): AskQuestionEvidence {
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
    const metadata = event.metadata ?? {};
    const toolUseId =
      typeof metadata.tool_use_id === "string" ? metadata.tool_use_id : "";
    if (!toolUseId) continue;
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
function resolveAskQuestion(
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

function withoutKey<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

export function useAskQuestionState({
  host,
  token,
  sessionId,
  snapshot,
  events,
  canReattach,
  reattach,
  onRequestError,
}: {
  host: string;
  token: string;
  sessionId: string;
  snapshot: PendingQuestionsSnapshot | null;
  events: EventRecord[];
  canReattach: boolean;
  reattach: () => void;
  onRequestError: (error: unknown, fallback: string) => void;
}): AskQuestionController {
  const [drafts, setDrafts] = useState<Record<string, AskDraft>>({});
  const [operations, setOperations] = useState<Record<string, AskOperation>>({});
  const operationsRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    setDrafts({});
    setOperations({});
    operationsRef.current.clear();
  }, [sessionId]);

  const evidence = useMemo(() => indexAskQuestionEvidence(events), [events]);

  const run = useCallback(
    async (
      toolUseId: string,
      operation: AskOperation,
      request: () => Promise<void>,
      fallback: string,
    ): Promise<boolean> => {
      if (operationsRef.current.has(toolUseId)) return false;
      operationsRef.current.add(toolUseId);
      setOperations((current) => ({ ...current, [toolUseId]: operation }));
      try {
        await request();
        setDrafts((current) => withoutKey(current, toolUseId));
        return true;
      } catch (requestError) {
        onRequestError(requestError, fallback);
        return false;
      } finally {
        operationsRef.current.delete(toolUseId);
        setOperations((current) => withoutKey(current, toolUseId));
      }
    },
    [onRequestError],
  );

  return useMemo<AskQuestionController>(
    () => ({
      snapshot,
      draft: (toolUseId) => drafts[toolUseId] ?? EMPTY_ASK_DRAFT,
      updateDraft: (toolUseId, update) =>
        setDrafts((current) => ({
          ...current,
          [toolUseId]: update(current[toolUseId] ?? EMPTY_ASK_DRAFT),
        })),
      operation: (toolUseId) => operations[toolUseId] ?? null,
      answer: (toolUseId, text, answers) =>
        run(
          toolUseId,
          "send",
          () => answerAskQuestion(host, token, sessionId, text, toolUseId, answers),
          "failed to send answer",
        ),
      cancel: (toolUseId) =>
        run(
          toolUseId,
          "cancel",
          () => cancelAskQuestion(host, token, sessionId, toolUseId),
          "failed to cancel question",
        ),
      resolution: (toolUseId) => resolveAskQuestion(toolUseId, evidence),
      canReattach,
      reattach,
    }),
    [
      canReattach,
      drafts,
      evidence,
      host,
      operations,
      reattach,
      run,
      sessionId,
      snapshot,
      token,
    ],
  );
}

// "loading": no snapshot yet. "gone": pending by loaded evidence but missing
// from a snapshot that already covers it — the provider ended it and its
// closure note is on the way.
export type AskFormState = PendingQuestionAvailability | "loading" | "gone" | "resolved";

export function askFormState(
  controller: AskQuestionController,
  event: EventRecord,
  resolution: AskQuestionResolution,
): AskFormState {
  if (resolution.state !== "pending") return "resolved";
  const { snapshot } = controller;
  if (!snapshot) return "loading";
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
    // A free-text question has no options; the typed text is the answer.
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

export function AskQuestionForm({
  event,
  questions,
  resolution,
}: {
  event: EventRecord;
  questions: AskUserQuestion[];
  resolution: AskQuestionResolution;
}) {
  const controller = useAskQuestionController();
  const toolUseId = askToolUseId(event);
  const draft = toolUseId ? controller.draft(toolUseId) : EMPTY_ASK_DRAFT;
  const updateDraft = (update: (current: AskDraft) => AskDraft) => {
    if (toolUseId) controller.updateDraft(toolUseId, update);
  };
  const operation = toolUseId ? controller.operation(toolUseId) : null;
  const formState = askFormState(controller, event, resolution);
  const open = formState !== "resolved" && formState !== "gone";
  const actionable = formState === "actionable" && toolUseId !== null;
  const editable = open && toolUseId !== null;
  const busy = operation !== null;

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
    if (!payload || !toolUseId) return;
    await controller.answer(toolUseId, payload.text, payload.answers);
  }

  function handleNoteKeyDown(keyEvent: KeyboardEvent<HTMLTextAreaElement>) {
    if (!isModifiedEnterShortcut(keyEvent)) return;
    keyEvent.preventDefault();
    void submit();
  }

  if (!entry) return null;
  const index = safeIndex;
  const selections = new Set(draft.picked[index] ?? []);
  const freeText = entry.options.length === 0;
  const promptLabel = questions[0]?.question ?? "this question";

  return (
    <>
      {formState === "starting" ? (
        <p className="ask-question-status">Waiting for the agent to accept a reply…</p>
      ) : formState === "unavailable" ? (
        <p className="ask-question-status warn">
          <span>
            {controller.canReattach
              ? "This session isn't running. Reattach it to answer or cancel."
              : "The agent can't take a reply to this question right now."}
          </span>
          {controller.canReattach ? (
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
        {freeText ? null : (
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
        )}
        {actionable ? (
          freeText || draft.notesOpen[index] ? (
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
                placeholder={
                  freeText ? "Type your answer…" : "Type your own answer or add a note here…"
                }
                aria-label={freeText ? `Answer: ${entry.question}` : undefined}
                rows={freeText ? 3 : 2}
                disabled={busy}
                aria-keyshortcuts="Meta+Enter Control+Enter"
              />
              {freeText ? null : (
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
              )}
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
          <button
            type="button"
            className="link-button ask-question-cancel"
            disabled={!actionable || busy}
            onClick={() => {
              if (toolUseId) void controller.cancel(toolUseId);
            }}
            aria-label={`Cancel question: ${promptLabel}`}
          >
            {operation === "cancel" ? "Cancelling…" : "Cancel question"}
          </button>
        </div>
      ) : null}
    </>
  );
}
