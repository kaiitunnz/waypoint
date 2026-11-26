"use client";

import { useState } from "react";

import { visibleOptions, type PromptPick } from "@waypoint/web-core/pendingAskActions";
import type { PendingAskPrompt } from "@waypoint/web-core/types";

import { ActionChip } from "@/components/ui/ActionChip";
import { Chip } from "@/components/ui/Chip";
import { TextInput } from "@/components/ui/Field";

import { OptionChips } from "./OptionChips";
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
      <OptionChips options={shown} selected={pick.labels} multi={prompt.multi_select} disabled={disabled} onOption={onOption}>
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
      </OptionChips>
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
