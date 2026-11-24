import type { ReactNode } from "react";

import styles from "./GlassBar.module.css";

export function GlassBar({ children }: { children: ReactNode }) {
  return (
    <header className={styles.bar}>
      <div className={styles.inner}>{children}</div>
    </header>
  );
}
