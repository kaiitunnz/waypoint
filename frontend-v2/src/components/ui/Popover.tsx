"use client";

import { useEffect, useRef, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";

import { usePopoverAnchor } from "@waypoint/web-core/hooks/use-popover-anchor";
import { usePopoverDismiss } from "@waypoint/web-core/hooks/use-popover-dismiss";
import { useResizablePopover } from "@waypoint/web-core/hooks/useResizablePopover";

import { cx } from "@/lib/cx";

import styles from "./Popover.module.css";
import { ResizeControls } from "./ResizeControls";

interface PopoverProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  triggerRef: RefObject<HTMLElement | null>;
  label: string;
  width?: number;
  align?: "left" | "right";
  // `top` for a trigger near the bottom edge.
  side?: "bottom" | "top";
  // The storage key that lets a person resize the panel and keeps their size.
  resizeKey?: string;
  // False leaves the focus where it is, for a panel a field's key opened.
  autoFocus?: boolean;
  children: ReactNode;
}

export function Popover({
  open,
  onOpenChange,
  triggerRef,
  label,
  width = 360,
  align = "left",
  side = "bottom",
  resizeKey,
  autoFocus = true,
  children,
}: PopoverProps) {
  const panel = useRef<HTMLDivElement>(null);
  const body = useRef<HTMLDivElement>(null);
  const anchor = usePopoverAnchor(triggerRef, open, align, { gap: 8 }, side === "top" ? "rise" : "dropdown");
  usePopoverDismiss(open, onOpenChange, triggerRef, panel, triggerRef);
  // The free edges are the ones away from the trigger.
  const direction = { width: align === "right" ? -1 : 1, height: side === "top" ? -1 : 1 } as const;
  const placed = open && anchor.style !== null;
  const { style: sized, resizable, custom, rendered, startResize, onResizeKey, reset } = useResizablePopover({
    storageKey: resizeKey ?? "",
    open: placed,
    enabled: resizeKey !== undefined,
    bounds: anchor.bounds,
    panelRef: panel,
    bodyRef: body,
    defaultWidth: width,
    direction,
  });
  // The panel is portaled to the end of the page, so focus moves into it for the keyboard.
  useEffect(() => {
    if (placed && autoFocus) panel.current?.focus({ preventScroll: true });
  }, [placed, autoFocus]);

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
        ...sized,
      }}
    >
      {resizable && anchor.bounds ? (
        <ResizeControls
          direction={direction}
          bounds={anchor.bounds}
          rendered={rendered}
          custom={custom}
          onStart={startResize}
          onKey={onResizeKey}
          onReset={reset}
        />
      ) : null}
      <div ref={body} className={styles.scroll}>
        {children}
      </div>
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
  disabled?: boolean;
}

export function MenuItem({ onSelect, children, selected, tone = "default", disabled }: MenuItemProps) {
  return (
    <button
      type="button"
      disabled={disabled}
      aria-current={selected || undefined}
      className={cx(styles.item, selected && styles.selected, tone === "danger" && styles.danger)}
      onClick={onSelect}
    >
      {children}
    </button>
  );
}
