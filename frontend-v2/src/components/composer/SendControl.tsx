"use client";

import { useEffect } from "react";

import { useHoldMenu } from "@waypoint/web-core/hooks/useHoldMenu";
import { SEND_MODES, type SendMode } from "@waypoint/web-core/session/sendModes";

import { Icon } from "@/components/ui/Icon";
import { cx } from "@/lib/cx";

import styles from "./SendControl.module.css";

interface SendControlProps {
  canSend: boolean;
  sending: boolean;
  // A stopped session that restarts on send: it cannot queue.
  dormant: boolean;
  onSend: (mode: SendMode) => void;
  // The pressed control is about to go or be disabled.
  keepFocus: () => void;
}

export function SendControl({ canSend, sending, dormant, onSend, keepFocus }: SendControlProps) {
  const modes = dormant ? SEND_MODES.filter((mode) => mode.value === "now") : SEND_MODES;
  const { open, close, dismiss, triggerRef, itemRefs, wrapRef, wrapProps, triggerProps, menuProps } = useHoldMenu(
    !canSend,
    () => {
      keepFocus();
      onSend("now");
    },
  );
  // Sending from the keyboard goes around this control, and disables it.
  useEffect(() => {
    if (!canSend) dismiss();
  }, [canSend, dismiss]);
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
          {modes.map((mode, index) => (
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
                keepFocus();
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
