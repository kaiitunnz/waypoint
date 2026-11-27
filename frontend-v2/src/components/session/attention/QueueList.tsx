"use client";

import { useState } from "react";

import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
import {
  heldCount,
  queueItemView,
  type QueueAction,
  type QueueItem,
} from "@waypoint/web-core/session/messageQueue";

import { Button } from "@/components/ui/Button";
import { cx } from "@/lib/cx";

import { Clamped } from "../cards/Clamped";
import styles from "./QueueList.module.css";

export interface QueueHandlers {
  onAction: (item: QueueItem, action: QueueAction) => Promise<void>;
  onReleaseAll: () => Promise<void>;
  onCancelAll: () => Promise<void>;
}

interface QueueListProps extends QueueHandlers {
  items: QueueItem[];
  focus: boolean;
}

const ACTION_LABEL: Record<QueueAction, string> = {
  release: "Release",
  sendNow: "Send now",
  cancel: "Cancel",
};

export function QueueList({ items, focus, onAction, onReleaseAll, onCancelAll }: QueueListProps) {
  // Ages and countdowns move on.
  useServerNow(30_000);
  const [busy, setBusy] = useState<Set<string>>(new Set());
  const [confirming, setConfirming] = useState(false);
  const [bulkBusy, setBulkBusy] = useState(false);
  const held = heldCount(items);

  const act = async (item: QueueItem, action: QueueAction) => {
    setBusy((current) => new Set(current).add(item.id));
    try {
      await onAction(item, action);
    } finally {
      setBusy((current) => {
        const next = new Set(current);
        next.delete(item.id);
        return next;
      });
    }
  };
  const bulk = async (run: () => Promise<void>) => {
    setBulkBusy(true);
    setConfirming(false);
    try {
      await run();
    } finally {
      setBulkBusy(false);
    }
  };

  return (
    <div className={styles.queue}>
      {held > 0 ? (
        <div className={styles.bulk}>
          {confirming ? (
            <>
              <span className={styles.confirm}>
                Cancel {held} held message{held === 1 ? "" : "s"}?
              </span>
              <Button variant="text" size="sm" onClick={() => setConfirming(false)}>
                Keep
              </Button>
              <Button variant="danger" size="sm" onClick={() => void bulk(onCancelAll)}>
                Cancel all
              </Button>
            </>
          ) : (
            <>
              <Button variant="text" size="sm" disabled={bulkBusy} onClick={() => void bulk(onReleaseAll)}>
                Release all
              </Button>
              <Button variant="danger" size="sm" disabled={bulkBusy} onClick={() => setConfirming(true)}>
                Cancel all
              </Button>
            </>
          )}
        </div>
      ) : null}
      <ul className={styles.list}>
        {items.map((item) => {
          const view = queueItemView(item, { focus });
          // Release all and Cancel all act on held messages only.
          const locked = (bulkBusy && item.kind === "held") || busy.has(item.id);
          return (
            <li key={`${item.kind}-${item.id}`} className={styles.item}>
              <div className={styles.meta}>
                <span className={styles.origin}>{view.origin}</span>
                <span>{view.when}</span>
                {view.cadence ? <span>{view.cadence}</span> : null}
              </div>
              {view.reason ? <p className={cx(styles.reason, view.manual && styles.manual)}>{view.reason}</p> : null}
              <Clamped lines={3} className={styles.text}>
                {view.text}
              </Clamped>
              <div className={styles.actions}>
                {view.actions.map((action) => (
                  <Button
                    key={action}
                    variant={action === "cancel" ? "danger" : "text"}
                    size="sm"
                    disabled={locked}
                    onClick={() => void act(item, action)}
                  >
                    {ACTION_LABEL[action]}
                  </Button>
                ))}
              </div>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
