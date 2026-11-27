"use client";

import { needsHref } from "@waypoint/web-core/needsYou";

import { DecisionAsk } from "@/components/sessions/DecisionAsk";
import { QuestionAsk } from "@/components/sessions/QuestionAsk";
import { SourceLine } from "@/components/sessions/SourceLine";
import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import { CheckMark } from "./CheckMark";
import { QueueItemShell, type QueueItemProps } from "./QueueItemShell";
import shell from "./QueueItemShell.module.css";
import styles from "./QueueCard.module.css";

export function QueueCard({ now, ...props }: QueueItemProps & { now: number }) {
  const { entry, view, time, selecting, checked } = props;

  if (entry.source === "sessions") {
    const href = needsHref(entry.routeId);
    return (
      <li className={cx(selecting && shell.inert)} inert={selecting}>
        {entry.ask.kind === "question" ? (
          <QuestionAsk ask={entry.ask} agent={view.kind} now={now} openHref={href} />
        ) : (
          <DecisionAsk ask={entry.ask} agent={view.kind} now={now} openHref={href} />
        )}
      </li>
    );
  }

  return (
    <QueueItemShell
      {...props}
      classes={{
        item: styles.item,
        target: cx(styles.card, view.strong && styles.strong, view.quiet && styles.quiet),
        behind: styles.behind,
        trash: styles.trash,
      }}
    >
      <SourceLine
        lead={selecting && entry.source === "inbox" ? <CheckMark checked={checked} /> : view.lead}
        name={view.name}
        kind={view.kind}
        time={<span className={styles.time}>{time}</span>}
      />
      <span className={styles.body}>
        <span className={styles.text}>
          <span className={styles.headline}>{view.headline}</span>
          {view.detail ? <span className={styles.detail}>{view.detail}</span> : null}
        </span>
        {selecting ? null : (
          <span className={styles.open}>
            Open
            <Icon name="chevron-right" size={15} strokeWidth={2} />
          </span>
        )}
      </span>
    </QueueItemShell>
  );
}
