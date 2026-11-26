import type { ReactNode } from "react";

import { cx } from "@/lib/cx";

import styles from "./SessionFooter.module.css";

interface SessionFooterProps {
  dock?: ReactNode;
  // The terminal's key bar, between the dock and the composer.
  keys?: ReactNode;
  composer?: ReactNode;
}

// Keeps the home-indicator inset even when it holds nothing.
export function SessionFooter({ dock, keys, composer }: SessionFooterProps) {
  return (
    <footer className={cx(styles.foot, composer || keys ? styles.filled : null)}>
      <div className={styles.col}>
        {dock}
        {keys}
        {composer}
      </div>
    </footer>
  );
}
