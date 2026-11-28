import type { ReactNode } from "react";

import styles from "./Section.module.css";

interface SectionProps {
  title: string;
  // Beside the title, muted: a count or a progress.
  count?: string;
  // A control at the end of the heading.
  action?: ReactNode;
  children: ReactNode;
}

// One block of the inspector: a heading, then its content.
export function Section({ title, count, action, children }: SectionProps) {
  return (
    <section className={styles.section} aria-label={title}>
      <header className={styles.head}>
        <h3 className={styles.title}>{title}</h3>
        {count ? <span className={styles.count}>{count}</span> : null}
        {action ? <span className={styles.action}>{action}</span> : null}
      </header>
      {children}
    </section>
  );
}
