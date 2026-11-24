import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import { StatusLamp, type LampState } from "./StatusLamp";
import styles from "./List.module.css";

export function List({ children }: { children: ReactNode }) {
  return <ul className={styles.list}>{children}</ul>;
}

interface ListRowProps {
  lamp?: LampState;
  title: ReactNode;
  titleFont?: "serif" | "sans";
  status?: ReactNode;
  trailing?: ReactNode;
  pinned?: boolean;
  // A selected row takes a fill, never an edge rail.
  selected?: boolean;
  // One-line row (idle, pinned, exited): no status line.
  compact?: boolean;
  dimmed?: boolean;
  onClick?: () => void;
}

export function ListRow({
  lamp,
  title,
  titleFont = "serif",
  status,
  trailing,
  pinned,
  selected,
  compact,
  dimmed,
  onClick,
}: ListRowProps) {
  const classes = cx(
    styles.row,
    !lamp && styles.noLamp,
    compact && styles.compact,
    dimmed && styles.dimmed,
    pinned && styles.pinned,
    selected && styles.selected,
  );
  return (
    <li className={styles.item}>
      <button type="button" className={classes} onClick={onClick} aria-current={selected ? "true" : undefined}>
        {lamp ? (
          <span className={styles.lamp}>
            <StatusLamp state={lamp} />
          </span>
        ) : null}
        <span className={styles.body}>
          <span className={cx(styles.title, titleFont === "sans" && styles.sans)}>{title}</span>
          {status ? <span className={styles.status}>{status}</span> : null}
        </span>
        {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
      </button>
    </li>
  );
}
