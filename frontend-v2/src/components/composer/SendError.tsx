import { ActionChip } from "@/components/ui/ActionChip";
import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { cx } from "@/lib/cx";

import type { SendFailure } from "./useComposerSend";
import styles from "./SendError.module.css";

interface SendErrorProps {
  failure: SendFailure;
  onRetry: () => void;
  onOpenSession: (sessionId: string) => void;
  onDismiss: () => void;
}

// A 409 is a passing state (a session still being restored), so it reads as a
// warning.
export function SendError({ failure, onRetry, onOpenSession, onDismiss }: SendErrorProps) {
  const opened = failure.openSessionId;
  return (
    <div className={cx(styles.notice, failure.status === 409 && styles.busy)} role="alert">
      <span className={styles.glyph}>
        <Icon name="alert" size={16} />
      </span>
      <p className={styles.message}>{failure.message}</p>
      {opened ? (
        <ActionChip icon="chevron-right" onClick={() => onOpenSession(opened)}>
          Open session
        </ActionChip>
      ) : (
        <ActionChip icon="refresh" onClick={onRetry}>
          Retry
        </ActionChip>
      )}
      <IconButton label="Dismiss" size="sm" onClick={onDismiss}>
        <Icon name="close" size={16} />
      </IconButton>
    </div>
  );
}
