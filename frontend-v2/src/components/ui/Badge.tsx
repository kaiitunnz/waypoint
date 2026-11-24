import { formatBadgeCount } from "@waypoint/web-core/shellNav";

import { cx } from "@/lib/cx";

import styles from "./Badge.module.css";

interface BadgeProps {
  count: number;
  max?: number;
  // A ring in the surface colour, for a badge that overlaps an icon.
  ringed?: boolean;
  className?: string;
}

export function Badge({ count, max = 99, ringed, className }: BadgeProps) {
  const text = formatBadgeCount(count, max);
  if (text === null) {
    return null;
  }
  return <span className={cx(styles.badge, ringed && styles.ringed, className)}>{text}</span>;
}
