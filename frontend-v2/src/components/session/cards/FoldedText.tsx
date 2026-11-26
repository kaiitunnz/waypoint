"use client";

import { cx } from "@/lib/cx";

import clamp from "./Clamp.module.css";
import { Disclosure } from "./Disclosure";
import styles from "./FoldedText.module.css";
import { useClamp } from "./useClamp";
import { useFold } from "./useFold";

interface FoldedTextProps {
  text: string;
  // Which end of the text stays in view while it is folded.
  keep: "start" | "end";
  className: string;
}

export function FoldedText({ text, keep, className }: FoldedTextProps) {
  const fold = useFold(text, keep);
  const { content, chip } = useClamp<HTMLSpanElement>(fold.open || fold.hidden > 0);
  const folded = keep === "end" ? clamp.keepEnd : clamp.clamp;
  return (
    <>
      <pre className={cx(className, fold.open && styles.open)}>
        <span className={fold.open ? clamp.content : folded}>
          <span ref={content} className={clamp.content}>
            {fold.shown}
          </span>
        </span>
      </pre>
      <div ref={chip} className={styles.fold}>
        <Disclosure open={fold.open} total={fold.hidden > 0 ? fold.total : undefined} onToggle={fold.toggle} />
      </div>
    </>
  );
}
