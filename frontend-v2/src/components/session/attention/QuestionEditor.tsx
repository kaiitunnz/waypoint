"use client";

import type { KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { stripRecommendedSuffix } from "@waypoint/web-core/pendingAskActions";
import {
  askFormView,
  isRecommendedLabel,
  setAskNote,
  setAskNoteOpen,
  stepAskQuestion,
  toggleLabel,
  type AskDraft,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";

import { ActionChip } from "@/components/ui/ActionChip";
import { Chip } from "@/components/ui/Chip";
import { TextArea } from "@/components/ui/Field";
import { cx } from "@/lib/cx";

import styles from "./QuestionEditor.module.css";

interface QuestionEditorProps {
  questions: AskUserQuestion[];
  draft: AskDraft;
  onDraft: (update: (draft: AskDraft) => AskDraft) => void;
  // Options and notes take no input.
  disabled: boolean;
  // Paging stays available while the answer cannot be edited.
  locked?: boolean;
  onSubmit: () => void;
}

// Answers an AskUserQuestion: one question at a time, options as chips, and a
// note beside them ("Other…") or a free-text answer.
export function QuestionEditor({ questions, draft, onDraft, disabled, locked = false, onSubmit }: QuestionEditorProps) {
  const { index, total, entry, paginated, filled, freeText } = askFormView(questions, draft);
  if (!entry) return null;
  const selected = new Set(draft.picked[index] ?? []);
  const noteOpen = freeText || Boolean(draft.notesOpen[index]);
  const multi = entry.multiSelect ?? false;

  const onNoteKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    onSubmit();
  };

  return (
    <div className={styles.editor}>
      {paginated ? (
        <div className={styles.pager}>
          <span className={styles.count}>
            Question {index + 1} of {total}
            {filled[index] ? " · answered" : ""}
          </span>
          <span className={styles.dots} aria-hidden>
            {questions.map((_, dot) => (
              <span key={dot} className={cx(styles.dot, dot === index && styles.current, filled[dot] && styles.filled)} />
            ))}
          </span>
        </div>
      ) : null}
      {paginated || entry.header ? (
        <p className={styles.question}>
          {entry.header ? <span className={styles.header}>{entry.header}</span> : null}
          {paginated ? entry.question : null}
        </p>
      ) : null}
      {multi ? <p className={styles.hint}>Multi-select</p> : null}
      {freeText ? null : (
        <div className={styles.chips} role="group" aria-label={multi ? "Choose any" : "Options"}>
          {entry.options.map((option) => (
            <Chip
              key={option.label}
              className={styles.option}
              recommended={isRecommendedLabel(option.label)}
              selected={selected.has(option.label)}
              disabled={disabled}
              onClick={() => onDraft((current) => toggleLabel(current, index, option.label, multi))}
            >
              <span className={styles.optionText}>
                <span>{stripRecommendedSuffix(option.label)}</span>
                {option.description ? <span className={styles.desc}>{option.description}</span> : null}
              </span>
            </Chip>
          ))}
          {noteOpen ? null : (
            <Chip quiet disabled={disabled} onClick={() => onDraft((current) => setAskNoteOpen(current, index, true))}>
              Other…
            </Chip>
          )}
        </div>
      )}
      {noteOpen ? (
        <div className={styles.note}>
          <TextArea
            autoFocus={!freeText}
            rows={freeText ? 3 : 2}
            value={draft.notes[index] ?? ""}
            disabled={disabled}
            aria-label={freeText ? `Answer: ${entry.question}` : "Other answer or note"}
            placeholder={freeText ? "Answer" : "Other"}
            onChange={(event) => {
              const value = event.target.value;
              onDraft((current) => setAskNote(current, index, value));
            }}
            onKeyDown={onNoteKey}
          />
          {freeText ? null : (
            <ActionChip
              icon="close"
              disabled={disabled}
              onClick={() => onDraft((current) => setAskNoteOpen(setAskNote(current, index, ""), index, false))}
            >
              Remove note
            </ActionChip>
          )}
        </div>
      ) : null}
      {paginated ? (
        <div className={styles.nav}>
          <ActionChip
            icon="chevron-left"
            disabled={locked || index === 0}
            onClick={() => onDraft((current) => stepAskQuestion(current, "prev", total))}
          >
            Previous
          </ActionChip>
          <ActionChip
            icon="chevron-right"
            disabled={locked || index === total - 1}
            onClick={() => onDraft((current) => stepAskQuestion(current, "next", total))}
          >
            Next
          </ActionChip>
        </div>
      ) : null}
    </div>
  );
}
