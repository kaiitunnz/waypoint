import { ActionChip } from "@/components/ui/ActionChip";
import { cx } from "@/lib/cx";

import styles from "./LoadFailed.module.css";

export function LoadFailed({ message, onRetry, className }: { message: string; onRetry: () => void; className?: string }) {
  return (
    <div className={cx(styles.failed, className)} role="alert">
      <p>{message}</p>
      <ActionChip icon="refresh" onClick={onRetry}>
        Retry
      </ActionChip>
    </div>
  );
}
