import type { SelectHTMLAttributes } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import styles from "./Field.module.css";

// A native select: touch clients get the system picker.
export function Select({ children, ...rest }: Omit<SelectHTMLAttributes<HTMLSelectElement>, "className">) {
  return (
    <span className={styles.wrap}>
      <select {...rest} className={cx(styles.input, styles.select)}>
        {children}
      </select>
      <span className={styles.caret} aria-hidden="true">
        <Icon name="chevron-down" size={16} />
      </span>
    </span>
  );
}
