import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./SessionFooter.module.css";

interface SessionFooterProps {
  dock?: ReactNode;
  composer?: ReactNode;
}

// Keeps the home-indicator inset even when it holds nothing.
export function SessionFooter({ dock, composer }: SessionFooterProps) {
  return (
    <footer className={cx(styles.foot, composer ? styles.filled : null)}>
      <div className={styles.col}>
        {dock}
        {composer}
      </div>
    </footer>
  );
}
