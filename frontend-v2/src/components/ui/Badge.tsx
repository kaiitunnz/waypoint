import { formatBadgeCount } from "@waypoint/web-core/shellNav";

import { cx } from "@/lib/cx";

import styles from "./Badge.module.css";

interface BadgeProps {
  count: number;
  // A ring in the ground colour, for a badge that overlaps an icon.
  ringed?: boolean;
  className?: string;
}

export function Badge({ count, ringed, className }: BadgeProps) {
  const text = formatBadgeCount(count);
  if (text === null) {
    return null;
  }
  return <span className={cx(styles.badge, ringed && styles.ringed, className)}>{text}</span>;
}
