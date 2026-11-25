"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { cx } from "@/lib/cx";

import styles from "./DensityToggle.module.css";

export type Density = "summary" | "full";

export const DENSITIES: readonly Density[] = ["summary", "full"];

const SEGMENTS: { value: Density; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "full", label: "Full" },
];

interface DensityToggleProps {
  value: Density;
  onChange: (value: Density) => void;
  // Text buttons in the desktop header, a segmented control in the mobile menu.
  variant: "text" | "segmented";
}

export function DensityToggle({ value, onChange, variant }: DensityToggleProps) {
  if (variant === "segmented") {
    return (
      <div className={styles.menuRow}>
        <SegmentedControl label="Transcript density" segments={SEGMENTS} value={value} onChange={onChange} />
      </div>
    );
  }
  return (
    <div role="radiogroup" aria-label="Transcript density" className={styles.toggle}>
      {SEGMENTS.map((segment) => (
        <button
          key={segment.value}
          type="button"
          role="radio"
          aria-checked={segment.value === value}
          className={cx(styles.option, segment.value === value && styles.on)}
          onClick={() => onChange(segment.value)}
        >
          {segment.label}
        </button>
      ))}
    </div>
  );
}
