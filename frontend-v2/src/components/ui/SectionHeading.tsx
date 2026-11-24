import type { ReactNode } from "react";

import styles from "./SectionHeading.module.css";

interface SectionHeadingProps {
  title: string;
  count?: number;
  action?: ReactNode;
}

export function SectionHeading({ title, count, action }: SectionHeadingProps) {
  return (
    <div className={styles.heading}>
      <h2 className={styles.title}>{title}</h2>
      {count !== undefined ? <span className={styles.count}>{count}</span> : null}
      {action ? <span className={styles.action}>{action}</span> : null}
    </div>
  );
}
