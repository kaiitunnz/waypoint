import { cx } from "@/lib/cx";

import styles from "./Badge.module.css";

interface BadgeProps {
  count: number;
  max?: number;
  // Spoken text; defaults to the count.
  label?: string;
  // A ring in the surface colour, for a badge that overlaps an icon.
  ringed?: boolean;
  className?: string;
}

// A needs-you count. Zero renders nothing.
export function Badge({ count, max = 99, label, ringed, className }: BadgeProps) {
  if (count <= 0) {
    return null;
  }
  return (
    <span className={cx(styles.badge, ringed && styles.ringed, className)} aria-label={label}>
      {count > max ? `${max}+` : count}
    </span>
  );
}
