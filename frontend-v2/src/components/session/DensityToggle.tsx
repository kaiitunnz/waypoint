"use client";

import { SegmentedControl } from "@/components/ui/SegmentedControl";

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
  // Inline in the desktop header, a full-width row in the mobile menu.
  variant: "text" | "segmented";
}

export function DensityToggle({ value, onChange, variant }: DensityToggleProps) {
  const control = <SegmentedControl label="Transcript density" segments={SEGMENTS} value={value} onChange={onChange} />;
  return variant === "segmented" ? <div className={styles.menuRow}>{control}</div> : control;
}
