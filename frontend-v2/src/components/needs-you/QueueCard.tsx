"use client";

import Link from "next/link";
import type { CSSProperties } from "react";

import { useSwipeAction } from "@waypoint/web-core/hooks/useSwipeAction";
import { needsHref } from "@waypoint/web-core/needsYou";

import { DecisionAsk } from "@/components/sessions/DecisionAsk";
import { QuestionAsk } from "@/components/sessions/QuestionAsk";
import { SourceLine } from "@/components/sessions/SourceLine";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { CheckMark } from "./CheckMark";
import type { QueueItemProps } from "./QueueRow";
import styles from "./QueueCard.module.css";

interface QueueCardProps extends Omit<QueueItemProps, "selected"> {
  agent: string;
  now: number;
}

// A card of the queue on its own column: asks answer in place, and the rest open.
export function QueueCard({ entry, view, time, selecting, checked, onToggle, onDelete, agent, now }: QueueCardProps) {
  const pointer = usePointerUI();
  const deletable = entry.source === "inbox";
  const checkable = selecting && deletable;
  const swipe = useSwipeAction({
    disabled: selecting || !deletable,
    onAction: () => onDelete(entry.key),
  });

  if (entry.source === "sessions") {
    const href = needsHref(entry.routeId);
    return (
      <li className={cx(styles.item, selecting && styles.inert)} inert={selecting}>
        {entry.ask.kind === "question" ? (
          <QuestionAsk ask={entry.ask} agent={agent} now={now} openHref={href} />
        ) : (
          <DecisionAsk ask={entry.ask} agent={agent} now={now} openHref={href} />
        )}
      </li>
    );
  }

  const content = (
    <>
      <SourceLine
        lead={checkable ? <CheckMark checked={checked} /> : view.lead}
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
    </>
  );
  const classes = cx(styles.card, view.strong && styles.strong, view.quiet && styles.quiet);

  return (
    <li className={cx(styles.item, selecting && !deletable && styles.inert, swipe.swiping && styles.swiping)}>
      {deletable ? (
        <span className={styles.behind} aria-hidden="true">
          <Icon name="trash" size={16} />
          Delete
        </span>
      ) : null}
      <div
        className={styles.slide}
        style={swipe.offset ? ({ transform: `translateX(${swipe.offset}px)` } as CSSProperties) : undefined}
        {...(deletable ? swipe.handlers : {})}
      >
        {checkable ? (
          <button type="button" role="checkbox" aria-checked={checked} className={classes} onClick={() => onToggle(entry.key)}>
            {content}
          </button>
        ) : selecting ? (
          <div className={classes} aria-disabled="true">
            {content}
          </div>
        ) : (
          <Link
            href={needsHref(entry.routeId)}
            draggable={false}
            className={classes}
            onClick={(event) => {
              if (swipe.consumeClick()) event.preventDefault();
            }}
          >
            {content}
          </Link>
        )}
      </div>
      {pointer && deletable && !selecting ? (
        <IconButton
          label={`Delete ${view.headline}`}
          size="sm"
          className={styles.trash}
          onClick={() => onDelete(entry.key)}
        >
          <Icon name="trash" size={15} />
        </IconButton>
      ) : null}
    </li>
  );
}
