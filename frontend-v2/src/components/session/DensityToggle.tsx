"use client";

import type { TranscriptDensity } from "@waypoint/web-core/session/transcriptView";

import { SegmentedControl } from "@/components/ui/SegmentedControl";

import styles from "./DensityToggle.module.css";

const SEGMENTS: { value: TranscriptDensity; label: string }[] = [
  { value: "summary", label: "Summary" },
  { value: "full", label: "Full" },
];

interface DensityToggleProps {
  value: TranscriptDensity;
  onChange: (value: TranscriptDensity) => void;
  // Inline in the desktop header, a full-width row in the mobile menu.
  variant: "text" | "segmented";
}

export function DensityToggle({ value, onChange, variant }: DensityToggleProps) {
  const control = (
    <SegmentedControl label="Transcript density" segments={SEGMENTS} value={value} onChange={onChange} size={variant === "text" ? "sm" : undefined} />
  );
  return variant === "segmented" ? <div className={styles.menuRow}>{control}</div> : control;
}
