"use client";

import type { ReactNode, RefObject } from "react";

import { usePointerUI } from "@/lib/layout";

import { Sheet } from "./Dialog";
import { Popover } from "./Popover";

interface FlyoutProps {
  open: boolean;
  onClose: () => void;
  triggerRef: RefObject<HTMLElement | null>;
  // The sheet's heading and the popover's spoken name.
  title: string;
  subtitle?: ReactNode;
  width?: number;
  align?: "left" | "right";
  side?: "bottom" | "top";
  // No padding around the sheet's body, for lists and menus.
  flush?: boolean;
  children: ReactNode;
}

// A popover from its trigger on a pointer client, a sheet on touch.
export function Flyout({ open, onClose, triggerRef, title, subtitle, width, align, side, flush, children }: FlyoutProps) {
  const pointer = usePointerUI();
  return pointer ? (
    <Popover
      open={open}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      triggerRef={triggerRef}
      label={title}
      width={width}
      align={align}
      side={side}
    >
      {children}
    </Popover>
  ) : (
    <Sheet open={open} onClose={onClose} title={title} subtitle={subtitle} flush={flush}>
      {children}
    </Sheet>
  );
}
