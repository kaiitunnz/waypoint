"use client";

import { useState } from "react";

import { stripRecommendedSuffix, type PromptPick } from "@waypoint/web-core/pendingAskActions";
import type { PendingAskPrompt } from "@waypoint/web-core/types";

import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Icon } from "@/components/ui/Icon";
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
  // Called when the Other field is submitted with Enter.
  onSubmitCustom?: () => void;
}

// One question's answers: option chips (the agent's recommended one marked), and
// an "Other…" field for a free-text answer.
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
  const [otherOpen, setOtherOpen] = useState(pick.other !== "");
  const limited = limit !== undefined && !expanded && prompt.options.length > limit;
  // Keep the recommended answer in view when the list is cut.
  const ordered = limited
    ? [...prompt.options].sort((a, b) => Number(b.recommended) - Number(a.recommended))
    : prompt.options;
  const shown = limited ? ordered.slice(0, limit) : ordered;

  return (
    <div className={styles.field}>
      {showQuestion ? <p className={styles.question}>{prompt.question}</p> : null}
      {prompt.multi_select ? <p className={styles.hint}>Choose any</p> : null}
      <div className={styles.chips}>
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
          <Button variant="text" size="sm" onClick={() => setExpanded(true)}>
            More
            <Icon name="chevron-down" size={15} strokeWidth={2} />
          </Button>
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
