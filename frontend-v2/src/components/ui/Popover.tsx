"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { usePopoverAnchor } from "@waypoint/web-core/hooks/use-popover-anchor";
import { usePopoverDismiss } from "@waypoint/web-core/hooks/use-popover-dismiss";

import { cx } from "@/lib/cx";

import styles from "./Popover.module.css";

interface PopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  triggerRef: RefObject<HTMLElement | null>;
  label: string;
  width?: number;
  // Which edge of the trigger the panel lines up with.
  align?: "left" | "right";
  children: ReactNode;
}

export function Popover({ open, onOpenChange, triggerRef, label, width = 360, align = "left", children }: PopoverProps) {
  const panel = useRef<HTMLDivElement>(null);
  const anchor = usePopoverAnchor(triggerRef, open, align, { gap: 8 });
  usePopoverDismiss(open, onOpenChange, triggerRef, panel, triggerRef);
  const placed = open && anchor.style !== null;
  // The panel is portaled to the end of the page, so focus moves into it for the keyboard.
  useEffect(() => {
    if (placed) panel.current?.focus({ preventScroll: true });
  }, [placed]);

  if (!open || typeof document === "undefined" || !anchor.style) {
    return null;
  }
  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={label}
      tabIndex={-1}
      className={styles.popover}
      style={{
        ...anchor.style,
        width: Math.min(width, anchor.bounds?.maxWidth ?? width),
        maxHeight: anchor.bounds?.maxHeight,
      }}
    >
      {children}
    </div>,
    document.body,
  );
}

export function Menu({ children, label }: { children: ReactNode; label: string }) {
  return (
    <div role="group" aria-label={label} className={styles.menu}>
      {children}
    </div>
  );
}

interface MenuItemProps {
  onSelect: () => void;
  children: ReactNode;
  selected?: boolean;
  tone?: "default" | "danger";
}

export function MenuItem({ onSelect, children, selected, tone = "default" }: MenuItemProps) {
  return (
    <button
      type="button"
      aria-current={selected || undefined}
      className={cx(styles.item, selected && styles.selected, tone === "danger" && styles.danger)}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}
