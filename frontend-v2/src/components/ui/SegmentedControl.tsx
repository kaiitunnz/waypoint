"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./SegmentedControl.module.css";

export interface Segment<T extends string> {
  value: T;
  label: ReactNode;
  // Spoken name when the label is an icon.
  ariaLabel?: string;
  count?: number;
}

interface SegmentedControlProps<T extends string> {
  segments: Segment<T>[];
  value: T;
  onChange: (value: T) => void;
  label: string;
  // `sm` is compact for a toolbar; `icon` is for segments that hold an icon.
  size?: "sm" | "icon";
  // Spans its container in equal segments.
  block?: boolean;
  disabled?: boolean;
}

export function SegmentedControl<T extends string>({
  segments,
  value,
  onChange,
  label,
  size,
  block,
  disabled,
}: SegmentedControlProps<T>) {
  const group = useRef<HTMLDivElement>(null);

  function onKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (disabled || event.altKey || event.ctrlKey || event.metaKey || event.shiftKey) return;
    const step = event.key === "ArrowRight" ? 1 : event.key === "ArrowLeft" ? -1 : 0;
    if (!step) return;
    event.preventDefault();
    const index = segments.findIndex((segment) => segment.value === value);
    const next = segments[(index + step + segments.length) % segments.length];
    onChange(next.value);
    group.current?.querySelector<HTMLElement>(`[data-value="${next.value}"]`)?.focus();
  }

  return (
    <div
      ref={group}
      role="radiogroup"
      aria-label={label}
      className={cx(styles.group, size && styles[size], block && styles.block)}
      onKeyDown={onKeyDown}
    >
      {segments.map((segment) => (
        <button
          key={segment.value}
          type="button"
          role="radio"
          aria-checked={segment.value === value}
          aria-label={segment.ariaLabel}
          data-value={segment.value}
          tabIndex={segment.value === value ? 0 : -1}
          disabled={disabled}
          className={cx(styles.segment, segment.value === value && styles.on)}
          onClick={() => onChange(segment.value)}
        >
          {typeof segment.label === "string" ? (
            <span className={styles.text} data-text={segment.label}>
              {segment.label}
            </span>
          ) : (
            segment.label
          )}
          {segment.count !== undefined ? <span className={styles.count}>{segment.count}</span> : null}
        </button>
      ))}
    </div>
  );
}
