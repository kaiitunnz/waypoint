"use client";

import type { KeyboardEvent } from "react";

import { isModifiedEnterShortcut } from "@waypoint/web-core/keyboard";
import { isRecommendedLabel } from "@waypoint/web-core/pendingAskActions";
import {
  askFormView,
  setAskNote,
  setAskNoteOpen,
  stepAskQuestion,
  toggleLabel,
  type AskDraft,
  type AskUserQuestion,
} from "@waypoint/web-core/session/askQuestion";

import { ActionChip } from "@/components/ui/ActionChip";
import { OptionChips } from "@/components/sessions/OptionChips";
import { Chip } from "@/components/ui/Chip";
import { TextArea } from "@/components/ui/Field";
import { cx } from "@/lib/cx";

import { OptionRows } from "./OptionRows";
import styles from "./QuestionEditor.module.css";

interface QuestionEditorProps {
  questions: AskUserQuestion[];
  // The card's heading shows the current question.
  headed: boolean;
  draft: AskDraft;
  onDraft: (update: (draft: AskDraft) => AskDraft) => void;
  disabled: boolean;
  // Locks paging too, while a request is in flight.
  locked: boolean;
  onSubmit: () => void;
  // `rows` lists the options as radio rows with the Other field always shown, for
  // one question shown on its own.
  variant?: "chips" | "rows";
}

// "Other…" opens a note kept beside the picked options, as Claude Code sends it.
export function QuestionEditor({
  questions,
  headed,
  draft,
  onDraft,
  disabled,
  locked,
  onSubmit,
  variant = "chips",
}: QuestionEditorProps) {
  const { index, total, entry, paginated, filled, freeText } = askFormView(questions, draft);
  if (!entry) return null;
  const noteOpen = freeText || Boolean(draft.notesOpen[index]);
  const multi = entry.multiSelect ?? false;

  const onNoteKey = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (!isModifiedEnterShortcut(event)) return;
    event.preventDefault();
    onSubmit();
  };

  if (variant === "rows") {
    return (
      <div className={styles.editor}>
        {entry.options.length > 0 ? (
          <OptionRows
            options={entry.options}
            selected={draft.picked[index] ?? []}
            multi={multi}
            disabled={disabled}
            label={entry.question}
            onOption={(label) => onDraft((current) => toggleLabel(current, index, label, multi))}
          />
        ) : null}
        <TextArea
          rows={1}
          value={draft.notes[index] ?? ""}
          disabled={disabled}
          aria-label="Other answer"
          placeholder="Other answer"
          onChange={(event) => {
            const value = event.target.value;
            onDraft((current) => setAskNote(current, index, value));
          }}
          onKeyDown={onNoteKey}
        />
      </div>
    );
  }

  return (
    <div className={styles.editor}>
      {paginated ? (
        <div className={styles.pager}>
          <span>
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
      {/* A lone question is the card's headline already. */}
      {paginated && !headed ? (
        <p className={styles.question}>
          {entry.header ? <span className={styles.header}>{entry.header}</span> : null}
          {entry.question}
        </p>
      ) : null}
      {multi ? <p className={styles.hint}>Multi-select</p> : null}
      {freeText ? null : (
        <OptionChips
          options={entry.options.map((option) => ({ ...option, recommended: isRecommendedLabel(option.label) }))}
          selected={draft.picked[index] ?? []}
          multi={multi}
          disabled={disabled}
          describe
          onOption={(label) => onDraft((current) => toggleLabel(current, index, label, multi))}
        >
          {noteOpen ? null : (
            <Chip quiet disabled={disabled} onClick={() => onDraft((current) => setAskNoteOpen(current, index, true))}>
              Other…
            </Chip>
          )}
        </OptionChips>
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
