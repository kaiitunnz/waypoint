"use client";

import { useRef, type MouseEvent, type ReactNode } from "react";

import { useDialog } from "@/components/ui/useDialog";
import { cx } from "@/lib/cx";

import styles from "./SideDrawer.module.css";

interface SideDrawerProps {
  side: "left" | "right";
  label: string;
  onClose: () => void;
  // A link in the drawer was followed in this tab.
  onFollow: (href: string) => void;
  children: ReactNode;
}

// A side panel drawn over the session, where the window has no room for it beside
// the conversation. Opaque, like every surface that carries text.
export function SideDrawer({ side, label, onClose, onFollow, children }: SideDrawerProps) {
  const panel = useRef<HTMLDivElement>(null);
  useDialog(true, onClose, panel);

  const onClick = (event: MouseEvent) => {
    if (event.metaKey || event.ctrlKey || event.shiftKey) return;
    const href = (event.target as Element).closest("a[href]")?.getAttribute("href");
    if (href) onFollow(href);
  };

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
        onClick={onClick}
      >
        {children}
      </div>
    </div>
  );
}
