"use client";

import { RefObject, useEffect } from "react";

// Closes a popover on an outside mousedown or Escape. The panel is checked
// separately from the wrapper because a portaled panel isn't its descendant.
export function usePopoverDismiss(
  open: boolean,
  setOpen: (open: boolean) => void,
  wrapRef: RefObject<HTMLElement | null>,
  panelRef: RefObject<HTMLElement | null>,
  triggerRef: RefObject<HTMLElement | null>,
): void {
  useEffect(() => {
    if (!open) return;
    function onDocClick(event: MouseEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (wrapRef.current?.contains(target)) return;
      if (panelRef.current?.contains(target)) return;
      // Focus returns to the trigger only from inside the panel, so a click
      // on another control keeps its own focus.
      const restoreFocus = panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (restoreFocus) triggerRef.current?.focus();
    }
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
        triggerRef.current?.focus();
      }
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen, wrapRef, panelRef, triggerRef]);
}
