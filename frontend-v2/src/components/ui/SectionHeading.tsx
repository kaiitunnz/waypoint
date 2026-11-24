import type { ReactNode } from "react";

import styles from "./SectionHeading.module.css";

interface SectionHeadingProps {
  title: string;
  count?: number;
  action?: ReactNode;
  as?: "h2" | "h3";
}

export function SectionHeading({ title, count, action, as: Tag = "h2" }: SectionHeadingProps) {
  return (
    <div className={styles.heading}>
      <Tag className={styles.title}>{title}</Tag>
      {count !== undefined ? <span className={styles.count}>{count}</span> : null}
      {action ? <span className={styles.action}>{action}</span> : null}
    </div>
  );
}
