"use client";

import { useState } from "react";

import { cx } from "@/lib/cx";

import { CopyButton } from "./CopyButton";
import { Disclosure } from "./Disclosure";
import { EventTime } from "./EventTime";
import { Markdown } from "./Markdown";
import styles from "./PlanCard.module.css";
import { useClamp } from "./useClamp";

export function PlanCard({ plan, ts }: { plan: string; ts: string }) {
  const [all, setAll] = useState(false);
  const { content, chip } = useClamp<HTMLDivElement>(all);
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.title}>Plan</span>
        <EventTime ts={ts} className={styles.time} />
        <CopyButton text={plan} label="Copy plan" />
      </div>
      <div className={cx(styles.body, !all && styles.clamped)}>
        <div ref={content}>
          <Markdown text={plan} />
        </div>
      </div>
      <div ref={chip} className={styles.more}>
        <Disclosure open={all} onToggle={() => setAll(!all)} />
      </div>
    </div>
  );
}
