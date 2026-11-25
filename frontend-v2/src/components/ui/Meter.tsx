import { cx } from "@/lib/cx";

import styles from "./Meter.module.css";

export type MeterTone = "working" | "calm" | "warn" | "danger";

interface MeterProps {
  // 0 to 100, or null when unknown.
  value: number | null;
  tone?: MeterTone;
  label: string;
  className?: string;
}

// A thin bar for task progress and limit pressure. Limit pressure uses warn and
// danger, never brass.
export function Meter({ value, tone = "working", label, className }: MeterProps) {
  // The empty track alone: a progressbar would announce zero.
  if (value === null) return <span aria-hidden="true" className={cx(styles.meter, className)} />;
  const percent = Math.min(100, Math.max(0, Math.round(value)));
  return (
    <span
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={percent}
      className={cx(styles.meter, styles[tone], className)}
    >
      <i style={{ width: `${percent}%` }} />
    </span>
  );
}
