"use client";

import Link from "next/link";
import type { ComponentProps, CSSProperties, ReactNode } from "react";

import { useSwipeAction } from "@waypoint/web-core/hooks/useSwipeAction";
import { needsHref, type QueueEntry } from "@waypoint/web-core/needsYou";

import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";
import { usePointerUI } from "@/lib/layout";

import type { EntryView } from "./entryView";
import styles from "./QueueItemShell.module.css";

export interface QueueItemProps {
  entry: QueueEntry;
  view: EntryView;
  time: string;
  // Select mode: inbox items take a checkbox, other entries wait.
  selecting: boolean;
  checked: boolean;
  onToggle: (id: string) => void;
  onDelete: (id: string) => void;
}

interface QueueItemShellProps extends Omit<QueueItemProps, "time"> {
  // The surface's own classes for each layer.
  classes: { item: string; target: string; slide?: string; behind?: string; trash: string };
  link?: Pick<ComponentProps<typeof Link>, "replace" | "scroll" | "aria-current">;
  children: ReactNode;
  // Below the target, inside the swiped layer.
  footer?: ReactNode;
}

// An entry that opens on tap. An inbox item also swipes or hovers to Delete, and
// takes a checkbox in select mode.
export function QueueItemShell({
  entry,
  view,
  selecting,
  checked,
  onToggle,
  onDelete,
  classes,
  link,
  children,
  footer,
}: QueueItemShellProps) {
  const pointer = usePointerUI();
  const deletable = entry.source === "inbox";
  const swipe = useSwipeAction({
    disabled: selecting || !deletable,
    onAction: () => onDelete(entry.key),
  });

  return (
    <li
      className={cx(styles.item, classes.item, selecting && !deletable && styles.inert, swipe.swiping && styles.swiping)}
    >
      {deletable ? (
        <span className={cx(styles.behind, classes.behind)} aria-hidden="true">
          <Icon name="trash" size={16} />
          Delete
        </span>
      ) : null}
      <div
        className={cx(styles.slide, classes.slide)}
        style={swipe.offset ? ({ transform: `translateX(${swipe.offset}px)` } as CSSProperties) : undefined}
        {...(deletable ? swipe.handlers : {})}
      >
        {selecting && deletable ? (
          <button type="button" role="checkbox" aria-checked={checked} className={classes.target} onClick={() => onToggle(entry.key)}>
            {children}
          </button>
        ) : selecting ? (
          <div className={classes.target} aria-disabled="true">
            {children}
          </div>
        ) : (
          <Link
            {...link}
            href={needsHref(entry.routeId)}
            draggable={false}
            className={classes.target}
            onClick={(event) => {
              if (swipe.consumeClick()) event.preventDefault();
            }}
          >
            {children}
          </Link>
        )}
        {footer}
      </div>
      {pointer && deletable && !selecting ? (
        <IconButton
          label={`Delete ${view.headline}`}
          size="sm"
          className={cx(styles.trash, classes.trash)}
          onClick={() => onDelete(entry.key)}
        >
          <Icon name="trash" size={15} />
        </IconButton>
      ) : null}
    </li>
  );
}
