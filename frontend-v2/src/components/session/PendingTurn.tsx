import type { PendingSend } from "@waypoint/web-core/hooks/useOptimisticSends";

import { cx } from "@/lib/cx";

import { Bubble } from "./cards/Bubble";
import styles from "./TurnView.module.css";

// The human's messages while they send.
export function PendingTurn({ messages }: { messages: PendingSend[] }) {
  return (
    <div role="group" aria-label="You, sending" className={cx(styles.group, styles.user, styles.pendingTurn)}>
      {messages.map((message) => (
        <Bubble
          key={message.id}
          tone="user"
          pending
          text={message.text || undefined}
          ts={message.ts}
          attachments={message.attachments}
        />
      ))}
    </div>
  );
}
