"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { EventTime } from "./EventTime";
import styles from "./SystemRule.module.css";

interface SystemRuleProps {
  text: string;
  ts: string;
  glyph?: string;
}

// One line; a note too long for it ends in an ellipsis and opens in place.
export function SystemRule({ text, ts, glyph }: SystemRuleProps) {
  const rule = useRef<HTMLDivElement>(null);
  const line = useRef<HTMLSpanElement>(null);
  const [clipped, setClipped] = useState(false);
  const [open, setOpen] = useState(false);

  useLayoutEffect(() => {
    const node = rule.current;
    if (!node || open || typeof ResizeObserver === "undefined") return;
    const measure = () => {
      const span = line.current;
      if (span) setClipped(span.scrollWidth > span.clientWidth);
    };
    measure();
    const observer = new ResizeObserver(measure);
    observer.observe(node);
    return () => observer.disconnect();
  }, [open, text]);

  const content = (
    <>
      {glyph ? <span aria-hidden="true">{glyph}</span> : null}
      <span ref={line} className={styles.text}>
        {text}
      </span>
    </>
  );
  return (
    <div ref={rule} className={cx(styles.rule, open && styles.open)} role="note">
      <span className={styles.body}>
        {open || clipped ? (
          <button
            type="button"
            className={styles.toggle}
            aria-expanded={open}
            title={open ? undefined : text}
            onClick={() => setOpen(!open)}
          >
            {content}
            <span className={styles.chev}>
              <Icon name={open ? "chevron-up" : "chevron-down"} size={12} strokeWidth={2} />
            </span>
          </button>
        ) : (
          content
        )}
        <EventTime ts={ts} className={styles.time} />
      </span>
    </div>
  );
}
