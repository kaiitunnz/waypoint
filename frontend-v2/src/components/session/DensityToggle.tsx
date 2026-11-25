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
  // Inline in the header, or a full-width row in the overflow menu.
  variant: "inline" | "menu";
}

export function DensityToggle({ value, onChange, variant }: DensityToggleProps) {
  const control = (
    <SegmentedControl label="Transcript density" segments={SEGMENTS} value={value} onChange={onChange} size={variant === "inline" ? "sm" : undefined} />
  );
  return variant === "menu" ? <div className={styles.menuRow}>{control}</div> : control;
}
