"use client";

import { AskQuestionOptions } from "@/components/TranscriptCard";
import type { InboxQuestionBlock } from "@/lib/types";

// Presentational, controlled variant of the transcript's AskUserQuestion card
// for an inbox question block (a single question). It reuses AskQuestionOptions
// for identical option rendering; selection/other state is owned by the parent
// block so the answer can be submitted together with the universal reply in one
// call. When `disabled` (already answered), options render read-only.
export function InboxQuestionCard({
  block,
  selected,
  other,
  onToggle,
  onOtherChange,
  disabled = false,
}: {
  block: InboxQuestionBlock;
  selected: Set<string>;
  other: string;
  onToggle: (label: string) => void;
  onOtherChange: (value: string) => void;
  disabled?: boolean;
}) {
  return (
    <div className="ask-question">
      <div className="ask-question-head">
        {block.header ? (
          <span className="badge neutral ask-question-chip">{block.header}</span>
        ) : null}
        <p className="ask-question-text">{block.question}</p>
        {block.multi ? <span className="meta">multi-select</span> : null}
        {block.required ? <span className="meta">required</span> : null}
      </div>
      <AskQuestionOptions
        options={block.options.map((option) => ({
          label: option.label,
          description: option.description ?? undefined,
        }))}
        selected={selected}
        onToggle={onToggle}
        disabled={disabled}
      />
      {disabled ? null : (
        <div className="ask-question-note">
          <textarea
            className="ask-question-note-input"
            value={other}
            onChange={(event) => onOtherChange(event.target.value)}
            placeholder="Other / custom answer (optional)…"
            rows={2}
          />
        </div>
      )}
    </div>
  );
}
