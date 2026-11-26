"use client";

import { cx } from "@/lib/cx";

import { AskRowActions } from "./AskRowActions";
import { CheckMark } from "./CheckMark";
import { QueueItemShell, type QueueItemProps } from "./QueueItemShell";
import styles from "./QueueRow.module.css";

export function QueueRow({ selected, ...props }: QueueItemProps & { selected: boolean }) {
  const { entry, view, time, selecting, checked } = props;

  return (
    <QueueItemShell
      {...props}
      classes={{
        item: cx(styles.row, selected && styles.selected, view.quiet && styles.quiet),
        target: cx(styles.main, view.strong && styles.strong, view.whole && styles.whole),
        slide: styles.slide,
        trash: styles.trash,
      }}
      link={{ replace: true, scroll: false, "aria-current": selected ? "true" : undefined }}
      footer={entry.source === "sessions" && !selecting ? <AskRowActions ask={entry.ask} /> : null}
    >
      <span className={styles.lead}>
        {selecting && entry.source === "inbox" ? <CheckMark checked={checked} /> : view.lead}
      </span>
      <span className={styles.source}>
        <span className={styles.name}>{view.name}</span>
        <span className={styles.kind}>{view.kind}</span>
      </span>
      <span className={styles.time}>{time}</span>
      <span className={styles.headline}>{view.headline}</span>
      {view.detail ? <span className={styles.detail}>{view.detail}</span> : null}
    </QueueItemShell>
  );
}
