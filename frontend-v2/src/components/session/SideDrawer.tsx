"use client";

import { useRef, type ReactNode } from "react";

import { useDialog } from "@/components/ui/useDialog";
import { cx } from "@/lib/cx";

import styles from "./SideDrawer.module.css";

interface SideDrawerProps {
  side: "left" | "right";
  label: string;
  onClose: () => void;
  children: ReactNode;
}

// A side panel drawn over the session, for a window with no room for it beside
// the conversation.
export function SideDrawer({ side, label, onClose, children }: SideDrawerProps) {
  const panel = useRef<HTMLDivElement>(null);
  useDialog(true, onClose, panel);

  return (
    <div className={styles.root}>
      <div className={styles.scrim} onClick={onClose} aria-hidden="true" />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-label={label}
        tabIndex={-1}
        data-drawer=""
        className={cx(styles.drawer, styles[side])}
      >
        {children}
      </div>
    </div>
  );
}
