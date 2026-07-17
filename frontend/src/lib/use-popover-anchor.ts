"use client";

import {
  CSSProperties,
  RefObject,
  useCallback,
  useLayoutEffect,
  useState,
} from "react";

// Where the popover is pinned relative to its trigger. ``dropdown`` drops the
// panel just below the trigger and is portaled to ``document.body`` with
// ``position: fixed`` (the term-bar case, whose trigger lives inside
// ``.session-terminal``'s ``overflow: hidden`` box). ``composer`` renders the
// panel in-tree above the trigger; CSS owns its ``position: absolute``
// bottom/right anchor, so the hook emits no position, only bounds.
export type PopoverPlacement = "dropdown" | "composer";

interface PopoverAnchorOptions {
  gap?: number;
  // Below this viewport width, return null style/bounds so a CSS bottom-sheet
  // media query (e.g. the generic ``.usage-panel`` mobile rule) takes over
  // instead of the fixed drop-down. Omit for popovers whose mobile styling is
  // specific to a different anchor and must not apply once portaled.
  deferBelow?: number;
  // Minimum inset kept between the panel and every visual-viewport edge.
  margin?: number;
}

// The space available for the panel to grow into, measured from its fixed
// anchor toward the free edges, in layout-viewport CSS pixels. ``SessionUsagePill``
// clamps a user's saved size against these bounds on every remeasurement.
export interface PopoverBounds {
  minWidth: number;
  maxWidth: number;
  minHeight: number;
  maxHeight: number;
}

export interface PopoverAnchor {
  // Fixed-position style for a ``dropdown`` placement; null for ``composer``
  // (CSS owns its anchor) and whenever the popover is closed or deferred to a
  // mobile media query.
  style: CSSProperties | null;
  // Placement-aware growth bounds; null when closed or deferred.
  bounds: PopoverBounds | null;
}

const CLOSED: PopoverAnchor = { style: null, bounds: null };

// Usable minimums the panel may shrink to before the available viewport is the
// tighter constraint; kept in sync with the RFC's proposed initial bounds.
const MIN_WIDTH = 320;
const MIN_HEIGHT = 240;

// Fixed-viewport coordinates and growth bounds for a popover anchored to a
// trigger. The dropdown placement positions the panel ``fixed`` just below the
// trigger and pins it to the trigger's left or right edge; the composer
// placement leaves positioning to CSS and only reports bounds. Both compute
// available space from the visual viewport so the panel never runs past a
// screen edge, and both re-measure while open so the panel tracks scroll,
// resize, and visual-viewport pan/zoom.
export function usePopoverAnchor(
  triggerRef: RefObject<HTMLElement | null>,
  open: boolean,
  align: "left" | "right",
  { gap = 10, deferBelow, margin = 8 }: PopoverAnchorOptions = {},
  placement: PopoverPlacement = "dropdown",
): PopoverAnchor {
  const [anchor, setAnchor] = useState<PopoverAnchor>(CLOSED);

  const measure = useCallback(() => {
    const el = triggerRef.current;
    if (!el) return;
    if (deferBelow !== undefined && window.innerWidth <= deferBelow) {
      setAnchor(CLOSED);
      return;
    }

    const vv = window.visualViewport;
    const visualLeft = vv?.offsetLeft ?? 0;
    const visualTop = vv?.offsetTop ?? 0;
    const visualWidth = vv?.width ?? window.innerWidth;
    const visualHeight = vv?.height ?? window.innerHeight;
    const visualRight = visualLeft + visualWidth;
    const visualBottom = visualTop + visualHeight;

    const rect = el.getBoundingClientRect();
    const triggerLeft = visualLeft + rect.left;
    const triggerTop = visualTop + rect.top;
    const triggerRight = visualLeft + rect.right;
    const triggerBottom = visualTop + rect.bottom;

    let availableWidth: number;
    let availableHeight: number;
    let style: CSSProperties | null;

    if (placement === "composer") {
      // Anchor bottom/right; grow toward the top/left viewport margins. The
      // panel's right edge tracks the trigger's right edge, its bottom edge
      // sits ``gap`` above the trigger's top edge.
      availableWidth = triggerRight - visualLeft - margin;
      availableHeight = triggerTop - gap - visualTop - margin;
      style = null;
    } else {
      // Anchor top/left; drop below the trigger and grow toward the
      // bottom/right viewport margins.
      const top = rect.bottom + gap;
      const common: CSSProperties = { position: "fixed", top, bottom: "auto" };
      style =
        align === "right"
          ? {
              ...common,
              right: Math.max(margin, window.innerWidth - rect.right),
              left: "auto",
            }
          : { ...common, left: Math.max(margin, rect.left), right: "auto" };
      availableWidth =
        align === "right"
          ? triggerRight - visualLeft - margin
          : visualRight - triggerLeft - margin;
      availableHeight = visualBottom - (triggerBottom + gap) - margin;
    }

    const maxWidth = Math.max(0, Math.round(availableWidth));
    const maxHeight = Math.max(0, Math.round(availableHeight));
    setAnchor({
      style,
      bounds: {
        maxWidth,
        maxHeight,
        minWidth: Math.min(MIN_WIDTH, maxWidth),
        minHeight: Math.min(MIN_HEIGHT, maxHeight),
      },
    });
  }, [triggerRef, align, gap, deferBelow, margin, placement]);

  useLayoutEffect(() => {
    if (!open) {
      setAnchor(CLOSED);
      return;
    }
    measure();
    window.addEventListener("scroll", measure, true);
    window.addEventListener("resize", measure);
    const vv = window.visualViewport;
    vv?.addEventListener("resize", measure);
    vv?.addEventListener("scroll", measure);
    return () => {
      window.removeEventListener("scroll", measure, true);
      window.removeEventListener("resize", measure);
      vv?.removeEventListener("resize", measure);
      vv?.removeEventListener("scroll", measure);
    };
  }, [open, measure]);

  return anchor;
}
