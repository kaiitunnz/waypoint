"use client";

import { CSSProperties, RefObject, useCallback, useLayoutEffect, useState } from "react";

interface PopoverAnchorOptions {
  gap?: number;
  // Below this viewport width, return null so a CSS bottom-sheet media query
  // (e.g. the generic ``.usage-panel`` mobile rule) takes over instead of the
  // fixed drop-down. Omit for popovers whose mobile styling is specific to a
  // different anchor and must not apply once portaled to the body.
  deferBelow?: number;
}

// Fixed-viewport coordinates for a popover dropped just below a trigger.
// Popovers that open from the term-bar live inside ``.session-terminal``'s
// ``overflow: hidden`` box, which clips an absolutely-positioned panel to the
// pane. Portaling the panel to ``document.body`` and positioning it ``fixed``
// against the trigger's rect escapes the clip entirely. ``align`` pins the
// panel to the trigger's left or right edge; ``max-height``/``overflow-y`` keep
// a tall panel scrollable rather than running off the viewport bottom. The
// coordinates are re-measured while open so the panel tracks scroll and resize.
export function usePopoverAnchor(
  triggerRef: RefObject<HTMLElement | null>,
  open: boolean,
  align: "left" | "right",
  { gap = 10, deferBelow }: PopoverAnchorOptions = {},
): CSSProperties | null {
  const [style, setStyle] = useState<CSSProperties | null>(null);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    if (deferBelow !== undefined && window.innerWidth <= deferBelow) {
      setStyle(null);
      return;
    }
    const rect = el.getBoundingClientRect();
    const top = rect.bottom + gap;
    const common: CSSProperties = {
      position: "fixed",
      top,
      bottom: "auto",
      maxHeight: `calc(100vh - ${Math.round(top)}px - 8px)`,
      overflowY: "auto",
    };
    setStyle(
      align === "right"
        ? { ...common, right: Math.max(8, window.innerWidth - rect.right), left: "auto" }
        : { ...common, left: Math.max(8, rect.left), right: "auto" },
    );
  }, [triggerRef, align, gap, deferBelow]);

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
