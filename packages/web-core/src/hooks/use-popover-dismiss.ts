"use client";

import { RefObject, useEffect } from "react";

// Open popover panels, innermost last.
const openPanels: RefObject<HTMLElement | null>[] = [];

// Closes a popover on an outside mousedown or Escape. The panel is checked
// separately from the wrapper because a portaled panel isn't its descendant, and
// a popover opened from inside this one counts as inside it.
export function usePopoverDismiss(
  open: boolean,
  setOpen: (open: boolean) => void,
  wrapRef: RefObject<HTMLElement | null>,
  panelRef: RefObject<HTMLElement | null>,
  triggerRef: RefObject<HTMLElement | null>,
  // For a popover that did not take the focus: Escape leaves it where it is
  // unless it has since moved into the panel.
  { keepOutsideFocus = false }: { keepOutsideFocus?: boolean } = {},
): void {
  // Its own effect, so re-renders keep the order popovers opened in.
  useEffect(() => {
    if (!open) return;
    openPanels.push(panelRef);
    return () => {
      openPanels.splice(openPanels.indexOf(panelRef), 1);
    };
  }, [open, panelRef]);

  useEffect(() => {
    if (!open) return;
    function onDocClick(event: MouseEvent) {
      const target = event.target as Node | null;
      if (!target) return;
      if (wrapRef.current?.contains(target)) return;
      const inner = openPanels.slice(openPanels.indexOf(panelRef));
      if (inner.some((panel) => panel.current?.contains(target))) return;
      // Focus returns to the trigger only from inside the panel, so a click
      // on another control keeps its own focus.
      const restoreFocus = panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (restoreFocus) triggerRef.current?.focus();
    }
    function onKey(event: KeyboardEvent) {
      // Only the innermost open popover answers Escape.
      if (event.key !== "Escape" || openPanels[openPanels.length - 1] !== panelRef) return;
      const stays = keepOutsideFocus && !panelRef.current?.contains(document.activeElement);
      setOpen(false);
      if (!stays) triggerRef.current?.focus();
    }
    document.addEventListener("mousedown", onDocClick);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDocClick);
      document.removeEventListener("keydown", onKey);
    };
  }, [open, setOpen, wrapRef, panelRef, triggerRef, keepOutsideFocus]);
}
