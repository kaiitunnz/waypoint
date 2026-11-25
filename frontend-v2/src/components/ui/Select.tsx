import type { Ref, SelectHTMLAttributes } from "react";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import styles from "./Field.module.css";

interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  ref?: Ref<HTMLSelectElement>;
}

// A native select: touch clients get the system picker.
export function Select({ className, children, ref, ...rest }: SelectProps) {
  return (
    <span className={cx(styles.wrap, className)}>
      <select {...rest} ref={ref} className={cx(styles.input, styles.select)}>
        {children}
      </select>
      <span className={styles.caret} aria-hidden="true">
        <Icon name="chevron-down" size={16} />
      </span>
    </span>
  );
}
