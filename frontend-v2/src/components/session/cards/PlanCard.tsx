"use client";

import { useLayoutEffect, useRef, useState } from "react";

import { cx } from "@/lib/cx";

import { CopyButton } from "./CopyButton";
import { Disclosure } from "./Disclosure";
import { EventTime } from "./EventTime";
import { Markdown } from "./Markdown";
import styles from "./PlanCard.module.css";

export function PlanCard({ plan, ts }: { plan: string; ts: string }) {
  const [all, setAll] = useState(false);
  const [clamped, setClamped] = useState(false);
  const body = useRef<HTMLDivElement>(null);
  useLayoutEffect(() => {
    const el = body.current;
    if (!el || all) return;
    const measure = () => setClamped(el.scrollHeight > el.clientHeight);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [plan, all]);
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.title}>Plan</span>
        <EventTime ts={ts} className={styles.time} />
        <CopyButton text={plan} label="Copy plan" />
      </div>
      <div ref={body} className={cx(styles.body, !all && styles.clamped)}>
        <Markdown text={plan} />
      </div>
      {clamped ? (
        <div className={styles.more}>
          <Disclosure open={all} onToggle={() => setAll(!all)} />
        </div>
      ) : null}
    </div>
  );
}
