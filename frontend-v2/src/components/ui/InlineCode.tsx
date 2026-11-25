import type { ReactNode } from "react";

import styles from "./InlineCode.module.css";

export function InlineCode({ children }: { children: ReactNode }) {
  return <code className={styles.code}>{children}</code>;
}
