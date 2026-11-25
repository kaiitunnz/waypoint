import type { ReactNode } from "react";

import styles from "./SessionFooter.module.css";

interface SessionFooterProps {
  dock?: ReactNode;
  composer?: ReactNode;
}

// Keeps the home-indicator inset even when it holds nothing.
export function SessionFooter({ dock, composer }: SessionFooterProps) {
  return (
    <footer className={styles.foot}>
      <div className={styles.col}>
        {dock}
        {composer}
      </div>
    </footer>
  );
}
