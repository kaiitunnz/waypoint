import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import { StatusLamp, type LampState } from "./StatusLamp";
import styles from "./List.module.css";

export function List({ children }: { children: ReactNode }) {
  return <ul className={styles.list}>{children}</ul>;
}

interface ListRowProps {
  lamp?: LampState;
  // Spoken name of the lamp, when no text beside it says the same.
  lampLabel?: string;
  title: ReactNode;
  titleFont?: "serif" | "sans";
  status?: ReactNode;
  trailing?: ReactNode;
  // A selected row takes a fill, never an edge rail.
  selected?: boolean;
  // One-line row (exited): no status line.
  compact?: boolean;
  // A session's read state; a row without it keeps the default type.
  unread?: boolean;
  dimmed?: boolean;
  href?: string;
  onClick?: () => void;
  // A control beside the row, such as its menu button; it is not part of the row's own link.
  actions?: ReactNode;
}

export function ListRow({
  lamp,
  lampLabel,
  title,
  titleFont = "serif",
  status,
  trailing,
  selected,
  compact,
  unread,
  dimmed,
  href,
  onClick,
  actions,
}: ListRowProps) {
  const classes = cx(
    styles.row,
    !lamp && styles.noLamp,
    compact && styles.compact,
    unread === true && styles.unread,
    unread === false && styles.read,
    dimmed && styles.dimmed,
    selected && styles.selected,
  );
  const content = (
    <>
      {lamp ? (
        <span className={styles.lamp}>
          <StatusLamp state={lamp} label={lampLabel} />
        </span>
      ) : null}
      <span className={styles.body}>
        <span className={cx(styles.title, titleFont === "sans" && styles.sans)}>{title}</span>
        {status ? <span className={styles.status}>{status}</span> : null}
      </span>
      {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
      {href ? (
        <span className={styles.chev} aria-hidden="true">
          <Icon name="chevron-right" size={16} strokeWidth={2} />
        </span>
      ) : null}
    </>
  );
  const current = selected ? "true" : undefined;
  return (
    <li className={cx(styles.item, Boolean(actions) && styles.withActions)}>
      {href ? (
        <Link href={href} className={classes} aria-current={current} onClick={onClick}>
          {content}
        </Link>
      ) : (
        <button type="button" className={classes} onClick={onClick} aria-current={current}>
          {content}
        </button>
      )}
      {actions ? <span className={styles.actions}>{actions}</span> : null}
    </li>
  );
}
