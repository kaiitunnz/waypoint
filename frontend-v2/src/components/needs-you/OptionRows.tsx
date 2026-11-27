"use client";

import { useRef, type KeyboardEvent } from "react";

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

export function OptionRows({ options, selected, multi, disabled, label, columns, onOption }: OptionRowsProps) {
  const group = useRef<HTMLDivElement>(null);
  const chosen = options.findIndex((option) => selected.includes(option.label));
  // A radio group is one tab stop, and the arrows move the choice within it.
  const tabStop = Math.max(chosen, 0);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (multi || disabled || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const step = ["ArrowRight", "ArrowDown"].includes(event.key) ? 1 : ["ArrowLeft", "ArrowUp"].includes(event.key) ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const radios = Array.from(group.current?.querySelectorAll<HTMLElement>('[role="radio"]') ?? []);
    const from = radios.indexOf(document.activeElement as HTMLElement);
    const next = (Math.max(from, 0) + step + options.length) % options.length;
    radios[next]?.focus();
    if (!selected.includes(options[next].label)) onOption(options[next].label);
  }

  return (
    <div
      ref={group}
      className={cx(styles.options, columns && styles.columns)}
      role={multi ? "group" : "radiogroup"}
      aria-label={label}
      onKeyDown={onKeyDown}
    >
      {options.map((option, index) => {
        const on = selected.includes(option.label);
        return (
          <button
            key={option.label}
            type="button"
            role={multi ? "checkbox" : "radio"}
            aria-checked={on}
            tabIndex={multi || index === tabStop ? 0 : -1}
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
