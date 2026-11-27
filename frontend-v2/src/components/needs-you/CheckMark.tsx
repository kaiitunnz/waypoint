import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./CheckMark.module.css";

// The box of a select-mode checkbox; the control around it carries the state.
export function CheckMark({ checked, mixed }: { checked: boolean; mixed?: boolean }) {
  return (
    <span className={cx(styles.box, (checked || mixed) && styles.on)} aria-hidden="true">
      {checked ? <Icon name="check" size={10} strokeWidth={3.5} /> : mixed ? <span className={styles.dash} /> : null}
    </span>
  );
}
