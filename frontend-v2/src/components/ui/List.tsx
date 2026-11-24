import Link from "next/link";
import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import { StatusLamp, type LampState } from "./StatusLamp";
import styles from "./List.module.css";

export function List({ children, className }: { children: ReactNode; className?: string }) {
  return <ul className={cx(styles.list, className)}>{children}</ul>;
}

interface ListRowProps {
  lamp?: LampState;
  // The one headline: a serif name for sessions, sans for plain labels.
  title: ReactNode;
  titleFont?: "serif" | "sans";
  // One muted line under the headline.
  status?: ReactNode;
  trailing?: ReactNode;
  // Pinned rows carry the brass dog-ear fold.
  pinned?: boolean;
  // A selected row takes the plane fill; there is never an edge rail.
  selected?: boolean;
  // Which surface the list sits on, so the selected fill contrasts with it.
  surface?: "plane" | "ground";
  // One-line row (idle, pinned, exited): no status line.
  compact?: boolean;
  dimmed?: boolean;
  href?: string;
  onClick?: () => void;
  className?: string;
}

export function ListRow({
  lamp,
  title,
  titleFont = "serif",
  status,
  trailing,
  pinned,
  selected,
  surface = "plane",
  compact,
  dimmed,
  href,
  onClick,
  className,
}: ListRowProps) {
  const classes = cx(
    styles.row,
    compact && styles.compact,
    dimmed && styles.dimmed,
    pinned && styles.pinned,
    selected && (surface === "ground" ? styles.selectedOnGround : styles.selected),
    className,
  );
  const content = (
    <>
      <span className={styles.lamp}>{lamp ? <StatusLamp state={lamp} /> : null}</span>
      <span className={styles.body}>
        <span className={cx(styles.title, titleFont === "sans" && styles.sans)}>{title}</span>
        {status ? <span className={styles.status}>{status}</span> : null}
      </span>
      {trailing ? <span className={styles.trailing}>{trailing}</span> : null}
    </>
  );
  return (
    <li className={styles.item}>
      {href ? (
        <Link href={href} className={classes} aria-current={selected ? "true" : undefined}>
          {content}
        </Link>
      ) : (
        <button type="button" className={classes} onClick={onClick} aria-current={selected ? "true" : undefined}>
          {content}
        </button>
      )}
    </li>
  );
}
