import { splitFileName } from "@waypoint/web-core/workspacePaths";

import { cx } from "@/lib/cx";

import styles from "./FileName.module.css";

// A file name on one line that gives up the middle of its name first: the extension and the
// characters before it stay.
export function FileName({ name, className }: { name: string; className?: string }) {
  const { head, tail } = splitFileName(name);
  return (
    <span className={cx(styles.name, className)}>
      {head ? <span className={styles.head}>{head}</span> : null}
      <span className={styles.tail}>{tail}</span>
    </span>
  );
}
