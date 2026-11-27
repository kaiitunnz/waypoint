"use client";

import { useState, type ReactNode } from "react";

import type { RunGlyph } from "@waypoint/web-core/session/toolRun";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import styles from "./RunLine.module.css";

interface RunLineProps {
  phrase: ReactNode;
  glyphs?: RunGlyph[];
  defaultOpen?: boolean;
  live?: boolean;
  asked?: boolean;
  // Changes the run made: kept whole while the phrase shortens, and in place of the
  // glyph counts in a narrow conversation.
  stats?: ReactNode;
  // Right-aligned text where the glyphs go, such as a time.
  trailing?: ReactNode;
  // A second line under the phrase.
  caption?: ReactNode;
  children: ReactNode;
}

export function RunLine({ phrase, glyphs = [], defaultOpen = false, live, asked, stats, trailing, caption, children }: RunLineProps) {
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? defaultOpen;
  const pointer = usePointerUI();
  const counted = (entry: RunGlyph) => `${entry.label} ${entry.count}`;
  const classes = cx(
    styles.line,
    live && styles.live,
    asked && styles.asked,
    Boolean(stats) && styles.hasStats,
    Boolean(caption) && styles.twoLine,
  );
  const inner = (
    <>
      <span className={styles.sum}>
        {caption ? (
          <>
            <span>{phrase}</span>
            <span className={styles.caption}>{caption}</span>
          </>
        ) : (
          phrase
        )}
      </span>
      {stats ? <span className={styles.stats}>{stats}</span> : null}
      {open ? null : (
        <span className={styles.glyphs}>
          {glyphs.map((entry) => (
            <span key={entry.glyph} role="img" aria-label={counted(entry)} title={counted(entry)}>
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
      {/* Touch has no hover to name the glyphs, so an open run spells its counts out. */}
      {open && !pointer && glyphs.length > 0 ? <p className={styles.counts}>{glyphs.map(counted).join(" · ")}</p> : null}
      {open ? <div className={styles.body}>{children}</div> : null}
    </div>
  );
}
