import { Icon } from "@/components/ui/Icon";

import styles from "./PinMark.module.css";

// A pinned session where no Pinned heading says so, such as a search result.
export function PinMark() {
  return (
    <span className={styles.pin} role="img" aria-label="Pinned">
      <Icon name="pin" size={13} />
    </span>
  );
}
