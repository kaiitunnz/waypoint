"use client";

import { isRecommendedLabel, stripRecommendedSuffix } from "@waypoint/web-core/pendingAskActions";

import { cx } from "@/lib/cx";

import styles from "./OptionRows.module.css";

export interface RowOption {
  // The answer value, "(Recommended)" included.
  label: string;
  description?: string | null;
}

interface OptionRowsProps {
  options: RowOption[];
  selected: readonly string[];
  multi: boolean;
  disabled?: boolean;
  // Names the group to assistive technology.
  label: string;
  // Side by side where there is room.
  columns?: boolean;
  onOption: (label: string) => void;
}

// Full-width radio rows, or checkboxes when several may be picked.
export function OptionRows({ options, selected, multi, disabled, label, columns, onOption }: OptionRowsProps) {
  return (
    <div className={cx(styles.options, columns && styles.columns)} role={multi ? "group" : "radiogroup"} aria-label={label}>
      {options.map((option) => {
        const on = selected.includes(option.label);
        return (
          <button
            key={option.label}
            type="button"
            role={multi ? "checkbox" : "radio"}
            aria-checked={on}
            disabled={disabled}
            className={cx(styles.option, on && styles.on)}
            onClick={() => onOption(option.label)}
          >
            <span className={cx(styles.mark, multi ? styles.box : styles.radio)} aria-hidden="true" />
            <span className={styles.text}>
              <span className={styles.label}>
                {stripRecommendedSuffix(option.label)}
                {isRecommendedLabel(option.label) ? <span className={styles.recommended}>Recommended</span> : null}
              </span>
              {option.description ? <span className={styles.description}>{option.description}</span> : null}
            </span>
          </button>
        );
      })}
    </div>
  );
}
