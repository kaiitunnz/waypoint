"use client";

import { createContext, useCallback, useContext, useEffect, useState, type KeyboardEvent } from "react";

import type { AskQuestionController } from "@waypoint/web-core/hooks/useAskQuestionState";
import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import {
  EMPTY_ASK_DRAFT,
  askFormState,
  askFormView,
  askToolUseId,
  clearAskAnswers,
  mergePendingQuestionSnapshot,
  serializeAnswers,
  setAskNote,
  setAskNoteOpen,
  stepAskQuestion,
  toggleLabel,
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

  const { index, total, entry, paginated, filled, hasAnswer, freeText } = askFormView(questions, draft);
  const canSubmit = actionable && !busy && hasAnswer;

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
  const selections = new Set(draft.picked[index] ?? []);
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
              {filled[index] ? " · answered" : ""}
            </span>
            <div className="ask-question-pager-dots" aria-hidden>
              {questions.map((_, dotIndex) => (
                <span
                  key={dotIndex}
                  className={`ask-question-pager-dot${
                    dotIndex === index ? " current" : ""
                  }${filled[dotIndex] ? " filled" : ""}`}
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
                  updateDraft((current) => setAskNote(current, index, value));
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
                  onClick={() => updateDraft((current) => setAskNoteOpen(current, index, false))}
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
              onClick={() => updateDraft((current) => setAskNoteOpen(current, index, true))}
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
              onClick={() => updateDraft((current) => stepAskQuestion(current, "prev", total))}
            >
              ← Previous
            </button>
            <button
              type="button"
              className="secondary"
              disabled={busy || index === total - 1}
              onClick={() => updateDraft((current) => stepAskQuestion(current, "next", total))}
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
          {hasAnswer ? (
            <button
              type="button"
              className="secondary"
              disabled={busy || !actionable}
              onClick={() => updateDraft(clearAskAnswers)}
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
