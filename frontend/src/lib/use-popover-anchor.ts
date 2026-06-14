"use client";

import { CSSProperties, RefObject, useCallback, useLayoutEffect, useState } from "react";

// Fixed-viewport coordinates for a popover dropped just below a trigger.
// Popovers that open from the term-bar live inside ``.session-terminal``'s
// ``overflow: hidden`` box, which clips an absolutely-positioned panel to the
// pane. Portaling the panel to ``document.body`` and positioning it ``fixed``
// against the trigger's rect escapes the clip entirely. ``align`` pins the
// panel to the trigger's left or right edge; the coordinates are re-measured
// while open so the panel tracks scroll and resize.
export function usePopoverAnchor(
  triggerRef: RefObject<HTMLElement | null>,
  open: boolean,
  align: "left" | "right",
  gap = 10,
): CSSProperties | null {
  const [style, setStyle] = useState<CSSProperties | null>(null);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    setStyle(
      align === "right"
        ? {
            position: "fixed",
            top: rect.bottom + gap,
            right: Math.max(8, window.innerWidth - rect.right),
            left: "auto",
            bottom: "auto",
          }
        : {
            position: "fixed",
            top: rect.bottom + gap,
            left: Math.max(8, rect.left),
            right: "auto",
            bottom: "auto",
          },
    );
  }, [triggerRef, align, gap]);

  useLayoutEffect(() => {
    if (!open) {
      setStyle(null);
      return;
    }
    measure();
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
    };
  }, [open, measure]);

  return style;
}
