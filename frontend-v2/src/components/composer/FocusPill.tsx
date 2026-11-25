"use client";

import { useRef, useState, type RefObject } from "react";

import { Button } from "@/components/ui/Button";
import { Flyout } from "@/components/ui/Flyout";
import { cx } from "@/lib/cx";

import hit from "@/styles/hit.module.css";
import styles from "./Composer.module.css";

interface FocusPillProps {
  busy: boolean;
  onTurnOff: () => Promise<void>;
  // Takes the focus once the pill is gone.
  returnFocusRef: RefObject<HTMLElement | null>;
}

// Shown while Focus holds incoming messages.
export function FocusPill({ busy, onTurnOff, returnFocusRef }: FocusPillProps) {
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);

  async function turnOff() {
    await onTurnOff();
    setOpen(false);
    // Deferred past the removal that drops the focus to the page.
    window.setTimeout(() => {
      if (document.activeElement === document.body) returnFocusRef.current?.focus();
    });
  }

  return (
    <>
      <button
        ref={trigger}
        type="button"
        className={cx(styles.focusPill, hit.hit)}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-label="Focus is on"
        onClick={() => setOpen((value) => !value)}
      >
        <span className={styles.focusMark} aria-hidden="true" />
        Focus
      </button>
      <Flyout
        open={open}
        onClose={() => setOpen(false)}
        triggerRef={trigger}
        title="Focus is on"
        width={280}
        side="top"
        align="right"
      >
        <div className={styles.focusPanel}>
          <p>Messages from agents and schedules are held until you release them.</p>
          <Button variant="secondary" busy={busy} onClick={() => void turnOff()}>
            {busy ? "Turning off…" : "Turn off Focus"}
          </Button>
        </div>
      </Flyout>
    </>
  );
}
