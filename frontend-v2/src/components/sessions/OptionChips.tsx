import type { ReactNode } from "react";

import { stripRecommendedSuffix } from "@waypoint/web-core/pendingAskActions";

import { Chip } from "@/components/ui/Chip";
import { cx } from "@/lib/cx";

import styles from "./OptionChips.module.css";

export interface ChipOption {
  // The answer value, "(Recommended)" included.
  label: string;
  description?: string | null;
  recommended: boolean;
}

interface OptionChipsProps {
  options: ChipOption[];
  selected: readonly string[];
  multi: boolean;
  disabled: boolean;
  // Descriptions under the labels; otherwise each is the chip's tooltip.
  describe?: boolean;
  onOption: (label: string) => void;
  // Chips after the options, such as Other….
  children?: ReactNode;
}

export function OptionChips({ options, selected, multi, disabled, describe, onOption, children }: OptionChipsProps) {
  return (
    <div className={styles.chips} role="group" aria-label={multi ? "Choose any" : "Options"}>
      {options.map((option) => (
        <Chip
          key={option.label}
          className={cx(describe && styles.described)}
          recommended={option.recommended}
          selected={selected.includes(option.label)}
          disabled={disabled}
          title={describe ? undefined : (option.description ?? undefined)}
          onClick={() => onOption(option.label)}
        >
          {describe ? (
            <span className={styles.text}>
              <span>{stripRecommendedSuffix(option.label)}</span>
              {option.description ? <span className={styles.description}>{option.description}</span> : null}
            </span>
          ) : (
            stripRecommendedSuffix(option.label)
          )}
        </Chip>
      ))}
      {children}
    </div>
  );
}
