"use client";

import { useState, type ReactNode } from "react";

import type { RunGlyph } from "@waypoint/web-core/session/toolRun";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./RunLine.module.css";

interface RunLineProps {
  phrase: ReactNode;
  glyphs?: RunGlyph[];
  defaultOpen?: boolean;
  live?: boolean;
  asked?: boolean;
  // Changes the run made: kept whole while the phrase shortens, and in place of the
  // glyph counts on a narrow screen.
  stats?: ReactNode;
  // Right-aligned text where the glyphs go, such as a time.
  trailing?: ReactNode;
  children: ReactNode;
}

export function RunLine({ phrase, glyphs = [], defaultOpen = false, live, asked, stats, trailing, children }: RunLineProps) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? defaultOpen;
  const classes = cx(styles.line, live && styles.live, asked && styles.asked, Boolean(stats) && styles.hasStats);
  const inner = (
    <>
      <span className={styles.sum}>{phrase}</span>
      {stats ? <span className={styles.stats}>{stats}</span> : null}
      {open ? null : (
        <span className={styles.glyphs}>
          {glyphs.map((entry) => (
            <span key={entry.glyph}>
              {entry.glyph} {entry.count}
            </span>
          ))}
        </span>
      )}
      {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
      <span className={styles.chev}>
        <Icon name={open ? "chevron-up" : "chevron-right"} size={14} strokeWidth={2} />
      </span>
    </>
  );
  return (
    <div className={styles.run}>
      <button type="button" className={classes} aria-expanded={open} onClick={() => setChoice(!open)}>
        {inner}
      </button>
      {open ? <div className={styles.body}>{children}</div> : null}
    </div>
  );
}
