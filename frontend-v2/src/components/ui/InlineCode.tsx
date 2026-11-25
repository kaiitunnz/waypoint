import type { ReactNode } from "react";

import styles from "./InlineCode.module.css";

// A machine string inside a line of prose: a command, a file name.
export function InlineCode({ children }: { children: ReactNode }) {
  return <code className={styles.code}>{children}</code>;
}
