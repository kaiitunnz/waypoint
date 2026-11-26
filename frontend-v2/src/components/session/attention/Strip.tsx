"use client";

import type { ReactNode, Ref } from "react";

import { Icon } from "@/components/ui/Icon";

import styles from "./Strip.module.css";

interface StripProps {
  glyph: ReactNode;
  label: ReactNode;
  meta?: ReactNode;
  count?: ReactNode;
  ariaLabel: string;
  open: boolean;
  onToggle: () => void;
  ref?: Ref<HTMLButtonElement>;
}

export function Strip({ glyph, label, meta, count, ariaLabel, open, onToggle, ref }: StripProps) {
  return (
    <button
      ref={ref}
      type="button"
      className={styles.strip}
      aria-expanded={open}
      aria-haspopup="dialog"
      aria-label={ariaLabel}
      onClick={onToggle}
    >
      <span className={styles.glyph} aria-hidden>
        {glyph}
      </span>
      <span className={styles.label}>{label}</span>
      {meta ? <span className={styles.meta}>{meta}</span> : null}
      {count !== undefined ? <span className={styles.count}>{count}</span> : null}
      <span className={styles.chevron} aria-hidden>
        <Icon name="chevron-up" size={16} />
      </span>
    </button>
  );
}
