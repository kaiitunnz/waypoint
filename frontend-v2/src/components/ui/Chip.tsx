import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./Chip.module.css";

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  // The agent's recommended answer.
  recommended?: boolean;
  // A text-only chip for the escape hatch ("Other…").
  quiet?: boolean;
  selected?: boolean;
  // Smaller on a pointer client.
  compact?: boolean;
  children: ReactNode;
}

export function Chip({ recommended, quiet, selected, compact, className, children, type = "button", ...rest }: ChipProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-pressed={selected}
      className={cx(
        styles.chip,
        recommended && styles.rec,
        quiet && styles.quiet,
        selected && styles.selected,
        compact && styles.compact,
        className,
      )}
    >
      {children}
      {recommended ? <span className={styles.label}>Recommended</span> : null}
    </button>
  );
}
