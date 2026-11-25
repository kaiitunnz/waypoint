"use client";

import { useHoldMenu } from "@waypoint/web-core/hooks/useHoldMenu";
import { SEND_MODES, type SendMode } from "@waypoint/web-core/session/sendModes";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./SendControl.module.css";

interface SendControlProps {
  canSend: boolean;
  sending: boolean;
  onSend: (mode: SendMode) => void;
}

// A tap sends now; a hover, a long press, ArrowDown or Space offers the idle
// deliveries.
export function SendControl({ canSend, sending, onSend }: SendControlProps) {
  const { open, close, triggerRef, itemRefs, wrapRef, wrapProps, triggerProps, menuProps } = useHoldMenu(
    !canSend,
    () => onSend("now"),
  );
  return (
    <div ref={wrapRef} className={styles.wrap} {...wrapProps}>
      <button
        ref={triggerRef}
        type="button"
        className={cx(styles.send, canSend && styles.ready)}
        disabled={!canSend}
        aria-label={sending ? "Sending…" : "Send"}
        aria-haspopup="menu"
        aria-expanded={open}
        // A held touch opens the send menu, not the system's.
        onContextMenu={(event) => event.preventDefault()}
        {...triggerProps}
      >
        {sending ? <span className={styles.spinner} aria-hidden="true" /> : <Icon name="arrow-up" size={20} strokeWidth={2.2} />}
      </button>
      {open ? (
        <div className={styles.menu} role="menu" aria-label="Send options" {...menuProps}>
          {SEND_MODES.map((mode, index) => (
            <button
              key={mode.value}
              ref={(node) => {
                itemRefs.current[index] = node;
              }}
              type="button"
              role="menuitem"
              className={styles.item}
              title={mode.hint}
              disabled={!canSend}
              onClick={() => {
                close();
                onSend(mode.value);
              }}
            >
              <Icon name={mode.value === "now" ? "arrow-up" : "clock"} size={16} />
              {mode.label}
            </button>
          ))}
        </div>
      ) : null}
    </div>
  );
}

export function StopButton({ stopping, onStop }: { stopping: boolean; onStop: () => void }) {
  return (
    <button
      type="button"
      className={styles.stop}
      onClick={onStop}
      aria-label={stopping ? "Stopping…" : "Stop"}
      aria-busy={stopping || undefined}
      title={stopping ? "Stopping…" : "Interrupt the agent"}
    >
      {stopping ? <span className={styles.spinner} aria-hidden="true" /> : <Icon name="stop" size={18} />}
    </button>
  );
}
