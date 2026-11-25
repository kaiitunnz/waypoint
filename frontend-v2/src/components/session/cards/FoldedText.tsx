"use client";

import { cx } from "@/lib/cx";

import { Disclosure } from "./Disclosure";
import styles from "./FoldedText.module.css";
import { useFold } from "./useFold";

interface FoldedTextProps {
  text: string;
  // Lines shown until it is opened, from the start or the end of the text.
  lines: number;
  keep: "start" | "end";
  className: string;
}

export function FoldedText({ text, lines, keep, className }: FoldedTextProps) {
  const fold = useFold(text, lines, keep);
  return (
    <>
      <pre className={cx(className, fold.open && styles.open)}>{fold.shown}</pre>
      {fold.foldable ? (
        <div className={styles.fold}>
          <Disclosure open={fold.open} total={fold.total} onToggle={fold.toggle} />
        </div>
      ) : null}
    </>
  );
}
