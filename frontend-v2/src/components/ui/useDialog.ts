"use client";

import { useEffect, useId, useRef, type RefObject } from "react";

import { TAB_STOP_SELECTOR, trapTabFocus } from "@waypoint/web-core/keyboard";

// Open dialogs, topmost last: only the topmost answers Escape, so a sheet opened
// from a modal closes alone.
const stack: string[] = [];
let scrollLocks = 0;

// Behaviour every dialog shares: focus moves in and returns to the opener on
// close, Tab stays inside, Escape closes the topmost, and the page behind does
// not scroll.
export function useDialog(open: boolean, onClose: () => void, panel: RefObject<HTMLElement | null>) {
  const id = useId();
  const close = useRef(onClose);
  useEffect(() => {
    close.current = onClose;
  });

  useEffect(() => {
    if (!open) {
      return;
    }
    const opener = document.activeElement as HTMLElement | null;
    stack.push(id);
    if (scrollLocks++ === 0) {
      document.body.style.overflow = "hidden";
    }
    const node = panel.current;
    const target = node?.querySelector<HTMLElement>("[data-autofocus]") ?? node;
    target?.focus({ preventScroll: true });

    function onKeyDown(event: KeyboardEvent) {
      if (stack[stack.length - 1] !== id) return;
      if (event.key === "Escape") {
        event.preventDefault();
        close.current();
        return;
      }
      trapTabFocus(event, panel.current, { preventWhenEmpty: true, fromNonStops: true, selector: TAB_STOP_SELECTOR });
    }
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      stack.splice(stack.indexOf(id), 1);
      if (--scrollLocks === 0) {
        document.body.style.overflow = "";
      }
      opener?.focus?.({ preventScroll: true });
    };
  }, [open, id, panel]);
}
