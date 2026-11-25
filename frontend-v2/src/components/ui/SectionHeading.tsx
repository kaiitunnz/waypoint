import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./SectionHeading.module.css";

interface SectionHeadingProps {
  title: string;
  count?: number;
  action?: ReactNode;
  dense?: boolean;
}

export function SectionHeading({ title, count, action, dense }: SectionHeadingProps) {
  return (
    <div className={cx(styles.heading, dense && styles.dense)}>
      <h2 className={styles.title}>{title}</h2>
      {count !== undefined ? <span className={styles.count}>{count}</span> : null}
      {action ? <span className={styles.action}>{action}</span> : null}
    </div>
  );
}
