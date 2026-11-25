"use client";

import { useMemo, useState } from "react";

import { foldLines } from "@waypoint/web-core/session/foldText";

import { cx } from "@/lib/cx";

import { Disclosure } from "./Disclosure";
import styles from "./FoldedText.module.css";

interface FoldedTextProps {
  text: string;
  // Lines shown until it is opened, from the start or the end of the text.
  lines: number;
  keep: "start" | "end";
  className: string;
}

// Preformatted text that shows `lines` of itself and opens to the whole of it in place.
export function FoldedText({ text, lines, keep, className }: FoldedTextProps) {
  const [open, setOpen] = useState(false);
  const folded = useMemo(() => foldLines(text, lines, keep), [text, lines, keep]);
  return (
    <>
      <pre className={cx(className, open && styles.open)}>{open ? text : folded.text}</pre>
      {folded.hidden > 0 ? (
        <div className={styles.fold}>
          <Disclosure open={open} total={folded.total} onToggle={() => setOpen(!open)} />
        </div>
      ) : null}
    </>
  );
}
