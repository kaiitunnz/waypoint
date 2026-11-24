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

import { answerAskQuestion, cancelAskQuestion } from "@waypoint/web-core/api";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import {
  EMPTY_ASK_DRAFT,
  askFormState,
  askToolUseId,
  indexAskQuestionEvidence,
  mergePendingQuestionSnapshot,
  resolveAskQuestion,
  serializeAnswers,
  toggleLabel,
  type AskAnswerEntry,
  type AskDraft,
  type AskQuestionOption,
  type AskQuestionResolution,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";
import type { EventRecord, PendingQuestionsSnapshot } from "@waypoint/web-core/types";

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

export function usePendingQuestionSnapshot(sessionId: string) {
  const [snapshot, setSnapshot] = useState<PendingQuestionsSnapshot | null>(null);
  useEffect(() => {
    setSnapshot(null);
  }, [sessionId]);
  const applySnapshot = useCallback(
    (next: PendingQuestionsSnapshot, replace: boolean) => {
      setSnapshot((current) => mergePendingQuestionSnapshot(current, next, replace));
    },
    [],
  );
  return { snapshot, applySnapshot };
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
  const formState = askFormState(controller.snapshot, event, resolution);
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
