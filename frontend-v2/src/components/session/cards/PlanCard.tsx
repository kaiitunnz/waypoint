"use client";

import { useState } from "react";

import { formatEventTime } from "@waypoint/web-core/session/cardText";

import { ActionChip } from "@/components/ui/ActionChip";
import { cx } from "@/lib/cx";

import { CopyButton } from "./CopyButton";
import { Markdown } from "./Markdown";
import styles from "./PlanCard.module.css";

export function PlanCard({ plan, ts }: { plan: string; ts: string }) {
  const [all, setAll] = useState(false);
  return (
    <div className={styles.card}>
      <div className={styles.head}>
        <span className={styles.title}>Plan</span>
        <time className={styles.time}>{formatEventTime(ts)}</time>
        <CopyButton text={plan} label="Copy plan" />
      </div>
      <div className={cx(styles.body, !all && styles.clamped)}>
        <Markdown text={plan} />
      </div>
      <div className={styles.more}>
        <ActionChip icon={all ? "chevron-up" : "chevron-down"} aria-expanded={all} onClick={() => setAll(!all)}>
          {all ? "Show less" : "Show all"}
        </ActionChip>
      </div>
    </div>
  );
}
