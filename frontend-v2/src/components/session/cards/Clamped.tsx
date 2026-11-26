"use client";

import { useState, type CSSProperties, type ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./Clamped.module.css";
import { Disclosure } from "./Disclosure";
import { useClamp } from "./useClamp";

interface ClampedProps {
  // Lines shown while folded.
  lines: number;
  // The disclosure's folded label.
  more?: string;
  className?: string;
  children: ReactNode;
}

export function Clamped({ lines, more, className, children }: ClampedProps) {
  const [all, setAll] = useState(false);
  const { content, chip } = useClamp<HTMLDivElement>(all);
  return (
    <>
      <div className={cx(className, !all && styles.clamped)} style={{ "--lines": lines } as CSSProperties}>
        <div ref={content}>{children}</div>
      </div>
      <div ref={chip} className={styles.more}>
        <Disclosure open={all} label={more} onToggle={() => setAll(!all)} />
      </div>
    </>
  );
}
