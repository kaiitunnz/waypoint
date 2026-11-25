"use client";

import { useState, type ReactNode } from "react";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./RunLine.module.css";

export interface RunGlyphCount {
  glyph: string;
  count: number;
}

interface RunLineProps {
  // What the run did, in plain words.
  phrase: ReactNode;
  glyphs?: RunGlyphCount[];
  // Whether the run opens by itself: in Full density.
  defaultOpen: boolean;
  // Running now.
  live?: boolean;
  // The ask the agent is waiting on.
  asked?: boolean;
  // Changes the run made: kept whole while the phrase shortens, and in place of the
  // glyph counts on a narrow screen.
  stats?: ReactNode;
  // Right-aligned text where the glyphs go, such as a time.
  trailing?: ReactNode;
  // Shown when the run is open; a run with nothing to open is a plain line.
  children?: ReactNode;
}

// One line on the route: a hollow marker, a plain summary, the glyph counts and a
// chevron. Opening it lists what the run held.
export function RunLine({ phrase, glyphs = [], defaultOpen, live, asked, stats, trailing, children }: RunLineProps) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const openable = children !== undefined && children !== null;
  const open = openable && (choice ?? defaultOpen);
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
      {openable ? (
        <span className={styles.chev}>
          <Icon name={open ? "chevron-up" : "chevron-right"} size={14} strokeWidth={2} />
        </span>
      ) : null}
    </>
  );
  return (
    <div className={styles.run}>
      {openable ? (
        <button type="button" className={classes} aria-expanded={open} onClick={() => setChoice(!open)}>
          {inner}
        </button>
      ) : (
        <div className={classes}>{inner}</div>
      )}
      {open ? <div className={styles.body}>{children}</div> : null}
    </div>
  );
}
