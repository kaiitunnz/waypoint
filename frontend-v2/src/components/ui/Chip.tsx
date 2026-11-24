import type { ButtonHTMLAttributes, ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./Chip.module.css";

interface ChipProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  // The agent's recommended answer inside a needs-you card: a brass-tinted outline and a quiet label.
  recommended?: boolean;
  // A text-only chip for the escape hatch ("Other…").
  quiet?: boolean;
  selected?: boolean;
  children: ReactNode;
}

export function Chip({ recommended, quiet, selected, className, children, type = "button", ...rest }: ChipProps) {
  return (
    <button
      {...rest}
      type={type}
      aria-pressed={selected}
      className={cx(styles.chip, recommended && styles.rec, quiet && styles.quiet, selected && styles.selected, className)}
    >
      {children}
      {recommended ? <span className={styles.label}>Recommended</span> : null}
    </button>
  );
}
