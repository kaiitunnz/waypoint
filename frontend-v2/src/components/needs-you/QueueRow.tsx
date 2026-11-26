"use client";

import Link from "next/link";
import type { CSSProperties } from "react";

import { useSwipeAction } from "@waypoint/web-core/hooks/useSwipeAction";
import { needsHref, type QueueEntry } from "@waypoint/web-core/needsYou";

import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import { AskRowActions } from "./AskRowActions";
import { CheckMark } from "./CheckMark";
import type { EntryView } from "./entryView";
import styles from "./QueueRow.module.css";

export interface QueueItemProps {
  entry: QueueEntry;
  view: EntryView;
  time: string;
  selected: boolean;
  // Select mode: inbox items take a checkbox, other rows wait.
  selecting: boolean;
  checked: boolean;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}

// A compact row of the queue beside the open item. A selected row takes a fill.
export function QueueRow({ entry, view, time, selected, selecting, checked, onToggle, onDelete }: QueueItemProps) {
  const pointer = usePointerUI();
  const deletable = entry.source === "inbox";
  const checkable = selecting && deletable;
  const inert = selecting && !deletable;
  const swipe = useSwipeAction({
    disabled: selecting || !deletable,
    onAction: () => onDelete(entry.key),
  });

  const content = (
    <>
      <span className={styles.lead}>{checkable ? <CheckMark checked={checked} /> : view.lead}</span>
      <span className={styles.source}>
        <span className={styles.name}>{view.name}</span>
        <span className={styles.kind}>{view.kind}</span>
      </span>
      <span className={styles.time}>{time}</span>
      <span className={styles.headline}>{view.headline}</span>
      {view.detail ? <span className={styles.detail}>{view.detail}</span> : null}
    </>
  );
  const classes = cx(styles.main, view.strong && styles.strong, view.whole && styles.whole);

  return (
    <li
      className={cx(
        styles.row,
        selected && styles.selected,
        view.quiet && styles.quiet,
        inert && styles.inert,
        swipe.swiping && styles.swiping,
      )}
    >
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
        ) : inert ? (
          <div className={classes} aria-disabled="true">
            {content}
          </div>
        ) : (
          <Link
            href={needsHref(entry.routeId)}
            replace
            scroll={false}
            draggable={false}
            className={classes}
            aria-current={selected ? "true" : undefined}
            onClick={(event) => {
              if (swipe.consumeClick()) event.preventDefault();
            }}
          >
            {content}
          </Link>
        )}
        {entry.source === "sessions" && !selecting ? (
          <div className={styles.actions}>
            <AskRowActions ask={entry.ask} />
          </div>
        ) : null}
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
