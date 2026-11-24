"use client";

import { useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { usePopoverAnchor } from "@waypoint/web-core/hooks/use-popover-anchor";
import { usePopoverDismiss } from "@waypoint/web-core/hooks/use-popover-dismiss";

import { cx } from "@/lib/cx";

import styles from "./Popover.module.css";

interface PopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  // The control that opens it; the panel anchors below it.
  triggerRef: RefObject<HTMLElement | null>;
  label: string;
  align?: "left" | "right";
  width?: number;
  children: ReactNode;
  className?: string;
}

// An anchored panel of floating chrome (glass), dismissed by an outside press or Escape.
export function Popover({
  open,
  onOpenChange,
  triggerRef,
  label,
  align = "left",
  width = 360,
  children,
  className,
}: PopoverProps) {
  const panel = useRef<HTMLDivElement>(null);
  const anchor = usePopoverAnchor(triggerRef, open, align, { gap: 8 });
  usePopoverDismiss(open, onOpenChange, triggerRef, panel, triggerRef);

  if (!open || typeof document === "undefined" || !anchor.style) {
    return null;
  }
  return createPortal(
    <div
      ref={panel}
      role="dialog"
      aria-label={label}
      className={cx(styles.popover, className)}
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
    <div role="menu" aria-label={label} className={styles.menu}>
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
      role="menuitem"
      className={cx(styles.item, selected && styles.selected, tone === "danger" && styles.danger)}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}
