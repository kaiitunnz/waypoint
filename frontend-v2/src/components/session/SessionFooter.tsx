import type { ReactNode } from "react";

import styles from "./SessionFooter.module.css";

interface SessionFooterProps {
  // Above the composer: what is waiting on the human.
  dock?: ReactNode;
  composer?: ReactNode;
}

// The bottom of the conversation. It always keeps the home-indicator inset.
export function SessionFooter({ dock, composer }: SessionFooterProps) {
  return (
    <footer className={styles.foot}>
      <div className={styles.col}>
        {dock ? <div data-slot="dock">{dock}</div> : null}
        {composer ? <div data-slot="composer">{composer}</div> : null}
      </div>
    </footer>
  );
}
