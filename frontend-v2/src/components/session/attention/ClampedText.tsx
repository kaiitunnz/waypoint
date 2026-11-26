"use client";

import { useState } from "react";

import { ActionChip } from "@/components/ui/ActionChip";
import { cx } from "@/lib/cx";

import { useClamp } from "../cards/useClamp";
import styles from "./ClampedText.module.css";

interface ClampedTextProps {
  text: string;
  className?: string;
  // The disclosure's label while folded.
  more?: string;
}

// Prose cut to a few lines, with a disclosure that shows it whole in place.
export function ClampedText({ text, className, more = "Show all" }: ClampedTextProps) {
  const [all, setAll] = useState(false);
  const { content, chip } = useClamp<HTMLDivElement>(all);
  return (
    <>
      <div className={cx(styles.box, !all && styles.clamped, className)}>
        <div ref={content}>{text}</div>
      </div>
      <div ref={chip} className={styles.chip}>
        <ActionChip icon={all ? "chevron-up" : "chevron-down"} aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? "Show less" : more}
        </ActionChip>
      </div>
    </>
  );
}
