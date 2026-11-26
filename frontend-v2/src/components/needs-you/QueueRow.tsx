"use client";

import { useAskCall } from "@/components/sessions/useAskCall";
import { cx } from "@/lib/cx";

import { AskRowActions } from "./AskRowActions";
import { CheckMark } from "./CheckMark";
import { entryView } from "./entryView";
import { QueueItemShell, type QueueItemProps } from "./QueueItemShell";
import styles from "./QueueRow.module.css";

interface QueueRowProps extends Omit<QueueItemProps, "view"> {
  selected: boolean;
  agentName: (backend: string) => string;
}

export function QueueRow({ selected, agentName, ...props }: QueueRowProps) {
  const { entry, time, selecting, checked } = props;
  const ask = entry.source === "sessions" ? entry.ask : null;
  const call = useAskCall(ask);
  const view = entryView(entry, agentName, call);

  return (
    <QueueItemShell
      {...props}
      view={view}
      classes={{
        item: cx(styles.row, selected && styles.selected, view.quiet && styles.quiet),
        target: cx(styles.main, view.strong && styles.strong, view.whole && styles.whole),
        slide: styles.slide,
        trash: styles.trash,
      }}
      link={{ replace: true, scroll: false, "aria-current": selected ? "true" : undefined }}
      footer={ask && !selecting ? <AskRowActions ask={ask} call={call} /> : null}
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
