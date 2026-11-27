"use client";

import { cx } from "@/lib/cx";

import clamp from "./Clamp.module.css";
import { Disclosure } from "./Disclosure";
import styles from "./FoldedText.module.css";
import { useFold } from "./useFold";

interface FoldedTextProps {
  text: string;
  className: string;
}

export function FoldedText({ text, className }: FoldedTextProps) {
  const { open, toggle, shown, total, content, chip } = useFold<HTMLSpanElement>(text);
  return (
    <>
      <pre className={cx(className, open && styles.open)}>
        <span className={open ? clamp.content : clamp.clamp}>
          <span ref={content} className={clamp.content}>
            {shown}
          </span>
        </span>
      </pre>
      <div ref={chip} className={styles.fold}>
        <Disclosure open={open} total={total} onToggle={toggle} />
      </div>
    </>
  );
}
