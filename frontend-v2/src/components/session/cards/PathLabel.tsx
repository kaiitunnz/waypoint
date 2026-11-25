import { cx } from "@/lib/cx";

import styles from "./PathLabel.module.css";

interface PathLabelProps {
  dir: string;
  base: string;
  className?: string;
  title?: string;
}

// A file path with its directory muted.
export function PathLabel({ dir, base, className, title }: PathLabelProps) {
  return (
    <span className={cx(styles.path, className)} title={title}>
      {dir ? <span className={styles.dir}>{dir}</span> : null}
      {base}
    </span>
  );
}
