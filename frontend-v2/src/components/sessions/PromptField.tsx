"use client";

import { useState } from "react";

import { stripRecommendedSuffix, visibleOptions, type PromptPick } from "@waypoint/web-core/pendingAskActions";
import type { PendingAskPrompt } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Chip } from "@/components/ui/Chip";
import { TextInput } from "@/components/ui/Field";

import styles from "./PromptField.module.css";

interface PromptFieldProps {
  prompt: PendingAskPrompt;
  pick: PromptPick;
  disabled: boolean;
  // Show only this many options until "More" is tapped.
  limit?: number;
  // The question text above the options, for a prompt shown without its own headline.
  showQuestion?: boolean;
  onOption: (label: string) => void;
  onCustom: (text: string) => void;
  onSubmitCustom?: () => void;
}

export function PromptField({
  prompt,
  pick,
  disabled,
  limit,
  showQuestion,
  onOption,
  onCustom,
  onSubmitCustom,
}: PromptFieldProps) {
  const [expanded, setExpanded] = useState(false);
  // With no options to choose, the free-text field is the whole question.
  const [otherOpen, setOtherOpen] = useState(pick.other !== "" || prompt.options.length === 0);
  const { shown, limited } = visibleOptions(prompt, limit, expanded);

  return (
    <div className={styles.field}>
      {showQuestion ? <p className={styles.question}>{prompt.question}</p> : null}
      <div className={styles.chips} role="group" aria-label={prompt.multi_select ? "Choose any" : "Options"}>
        {shown.map((option) => (
          <Chip
            key={option.label}
            recommended={option.recommended}
            selected={pick.labels.includes(option.label)}
            disabled={disabled}
            title={option.description ?? undefined}
            onClick={() => onOption(option.label)}
          >
            {stripRecommendedSuffix(option.label)}
          </Chip>
        ))}
        {limited ? (
          <ActionChip icon="chevron-down" onClick={() => setExpanded(true)}>
            More
          </ActionChip>
        ) : null}
        {otherOpen ? null : (
          <Chip quiet disabled={disabled} onClick={() => setOtherOpen(true)}>
            Other…
          </Chip>
        )}
      </div>
      {otherOpen ? (
        <TextInput
          autoFocus
          value={pick.other}
          disabled={disabled}
          aria-label="Your answer"
          placeholder="Your answer"
          onChange={(event) => onCustom(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmitCustom?.();
            }
          }}
        />
      ) : null}
    </div>
  );
}
