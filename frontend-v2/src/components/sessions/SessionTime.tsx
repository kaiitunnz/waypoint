import { Icon } from "@/components/ui/Icon";

import styles from "./SessionTime.module.css";

interface SessionTimeProps {
  time: string;
  pinned?: boolean;
  // The lamp's label already says unread, so the dot is silent.
  unread?: boolean;
}

// A session row's time, taking its colour from the row.
export function SessionTime({ time, pinned, unread }: SessionTimeProps) {
  return (
    <span className={styles.when}>
      {pinned ? (
        <span className={styles.pin} role="img" aria-label="Pinned">
          <Icon name="pin" size={13} />
        </span>
      ) : null}
      {unread ? <span className={styles.dot} aria-hidden="true" /> : null}
      <span className={styles.time}>{time}</span>
    </span>
  );
}
