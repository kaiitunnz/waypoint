"use client";

import type { ReactNode, RefObject } from "react";

import { usePointerUI } from "@/lib/layout";

import { Sheet } from "./Dialog";
import styles from "./Flyout.module.css";
import { Popover } from "./Popover";

interface FlyoutProps {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLElement | null>;
  // The sheet's heading, and the popover's spoken name unless `label` is given.
  title: string;
  label?: string;
  subtitle?: ReactNode;
  width?: number;
  align?: "left" | "right";
  side?: "bottom" | "top";
  // Lets a pointer client resize the popover; a sheet has one size.
  resizeKey?: string;
  // False keeps the focus out of the popover. A sheet is modal and always takes it.
  autoFocus?: boolean;
  // No padding around the sheet's body, for lists and menus.
  flush?: boolean;
  // A panel's heading: a count beside the title and controls after it. The popover
  // then shows the title and pads its body, as a sheet does.
  count?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
}

// A popover from its trigger on a pointer client, a sheet on touch.
export function Flyout({
  open,
  onClose,
  triggerRef,
  title,
  label,
  subtitle,
  width,
  align,
  side,
  resizeKey,
  autoFocus,
  flush,
  count,
  actions,
  children,
}: FlyoutProps) {
  const pointer = usePointerUI();
  const panel = count !== undefined || actions !== undefined;
  return pointer ? (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      triggerRef={triggerRef}
      label={label ?? title}
      width={width}
      align={align}
      side={side}
      resizeKey={resizeKey}
      autoFocus={autoFocus}
    >
      {panel ? (
        <div className={styles.panel}>
          <div className={styles.head}>
            <h2 className={styles.title}>
              {title}
              {count !== undefined ? <span className={styles.count}>{count}</span> : null}
            </h2>
            {actions}
          </div>
          {children}
        </div>
      ) : (
        children
      )}
    </Popover>
  ) : (
    <Sheet open={open} onClose={onClose} title={title} subtitle={subtitle} flush={flush}>
      {actions ? <div className={styles.actions}>{actions}</div> : null}
      {children}
    </Sheet>
  );
}
