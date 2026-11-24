import type { ReactNode } from "react";

import styles from "./EmptyState.module.css";

interface EmptyStateProps {
  title: string;
  detail?: ReactNode;
}

export function EmptyState({ title, detail }: EmptyStateProps) {
  return (
    <div className={styles.empty}>
      <p className={styles.title}>{title}</p>
      {detail ? <p className={styles.detail}>{detail}</p> : null}
    </div>
  );
}
