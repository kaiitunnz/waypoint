import type { ReactNode } from "react";

import styles from "./PageHeader.module.css";

interface PageHeaderProps {
  title: string;
  // A muted line under the title.
  subtitle?: ReactNode;
  // Controls to the right of the title (host chip, actions).
  actions?: ReactNode;
}

// The in-flow header of a top-level screen: the page title in serif, padded
// below the status bar and Dynamic Island.
export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <header className={styles.header}>
      <div className={styles.titles}>
        <h1 className={styles.title}>{title}</h1>
        {subtitle ? <div className={styles.subtitle}>{subtitle}</div> : null}
      </div>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
    </header>
  );
}
