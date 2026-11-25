"use client";

import {
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type PointerEvent as ReactPointerEvent,
  type RefObject,
} from "react";

import {
  clampSize,
  keyResizeDelta,
  readPopoverSize,
  resizedDimension,
  writePopoverSize,
  type PopoverSize,
  type ResizeAxis,
  type ResizeDirection,
} from "../popoverSize";
import type { PopoverBounds } from "./use-popover-anchor";

interface ResizablePopoverOptions {
  storageKey: string;
  open: boolean;
  // False where the popover has no resize controls (a sheet).
  enabled: boolean;
  bounds: PopoverBounds | null;
  panelRef: RefObject<HTMLElement | null>;
  // The scrolling content inside the panel, whose height is the default height.
  bodyRef: RefObject<HTMLElement | null>;
  defaultWidth: number;
  direction: ResizeDirection;
  // The axis a pointer is dragging, then null. Must be stable.
  onResizing?: (axis: ResizeAxis | null) => void;
}

export interface PopoverSizeStyle {
  maxWidth: number;
  maxHeight: number;
  width?: number;
  height?: number;
}

export interface ResizablePopover {
  // The panel's size, within the space it has; null while it is not resizable.
  style: PopoverSizeStyle | null;
  resizable: boolean;
  custom: boolean;
  // The size on screen, for the controls' spoken values.
  rendered: { width: number; height: number } | null;
  startResize: (axis: ResizeAxis) => (event: ReactPointerEvent) => void;
  onResizeKey: (axis: ResizeAxis) => (event: KeyboardEvent) => void;
  reset: () => void;
}

// A popover a person can resize by its free edges, remembered across openings.
// The stored preference is kept as given and shown clamped to the space there
// is, so an oversized one expands again when space permits.
export function useResizablePopover({
  storageKey,
  open,
  enabled,
  bounds,
  panelRef,
  bodyRef,
  defaultWidth,
  direction,
  onResizing,
}: ResizablePopoverOptions): ResizablePopover {
  const [pref, setPref] = useState<PopoverSize>(() => readPopoverSize(storageKey));
  // What the pointer and key handlers merge into.
  const prefRef = useRef(pref);
  const [rendered, setRendered] = useState<{ width: number; height: number } | null>(null);
  const { width: widthDirection, height: heightDirection } = direction;

  // Read again on open, for a size set by another popover with the same key.
  useEffect(() => {
    if (!open) return;
    const stored = readPopoverSize(storageKey);
    prefRef.current = stored;
    setPref(stored);
  }, [open, storageKey]);

  const apply = useCallback(
    (patch: PopoverSize) => {
      const next = { ...prefRef.current, ...patch };
      prefRef.current = next;
      setPref(next);
      writePopoverSize(storageKey, next);
    },
    [storageKey],
  );

  const reset = useCallback(() => {
    prefRef.current = {};
    setPref({});
    writePopoverSize(storageKey, {});
  }, [storageKey]);

  // The default a resize snaps to: the default width, and the content's natural
  // height, which never follows a panel dragged past its content.
  const fallback = useCallback(
    (axis: ResizeAxis): number | null => {
      if (!bounds) return null;
      if (axis === "width") {
        return clampSize(Math.min(defaultWidth, bounds.maxWidth), bounds.minWidth, bounds.maxWidth);
      }
      const panel = panelRef.current;
      const body = bodyRef.current;
      if (!panel || !body) return null;
      const style = window.getComputedStyle(panel);
      const chrome = body.offsetTop + parseFloat(style.paddingBottom) + parseFloat(style.borderBottomWidth);
      return clampSize(chrome + body.scrollHeight, bounds.minHeight, bounds.maxHeight);
    },
    [bounds, defaultWidth, panelRef, bodyRef],
  );

  const resize = useCallback(
    (axis: ResizeAxis, value: number, target: number | null) => {
      if (!bounds) return;
      const [min, max] =
        axis === "width" ? [bounds.minWidth, bounds.maxWidth] : [bounds.minHeight, bounds.maxHeight];
      apply({ [axis]: resizedDimension(value, min, max, target) });
    },
    [bounds, apply],
  );

  const startResize = useCallback(
    (axis: ResizeAxis) => (event: ReactPointerEvent) => {
      const panel = panelRef.current;
      if (!bounds || !panel) return;
      event.preventDefault();
      const start = axis === "width" ? event.clientX : event.clientY;
      const startSize = axis === "width" ? panel.offsetWidth : panel.offsetHeight;
      const sign = axis === "width" ? widthDirection : heightDirection;
      onResizing?.(axis);
      // Taken once, so the detent holds still for the whole drag.
      const target = fallback(axis);
      const onMove = (move: PointerEvent) => {
        const position = axis === "width" ? move.clientX : move.clientY;
        resize(axis, startSize + sign * (position - start), target);
      };
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        onResizing?.(null);
      };
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [bounds, panelRef, widthDirection, heightDirection, fallback, resize, onResizing],
  );

  const onResizeKey = useCallback(
    (axis: ResizeAxis) => (event: KeyboardEvent) => {
      if (!bounds) return;
      const delta = keyResizeDelta(axis, event.key, event.shiftKey, {
        width: widthDirection,
        height: heightDirection,
      });
      if (delta === null) return;
      event.preventDefault();
      const panel = panelRef.current;
      const current =
        axis === "width"
          ? (prefRef.current.width ?? panel?.offsetWidth ?? bounds.minWidth)
          : (prefRef.current.height ?? panel?.offsetHeight ?? bounds.minHeight);
      resize(axis, current + delta, fallback(axis));
    },
    [bounds, panelRef, widthDirection, heightDirection, resize, fallback],
  );

  const resizable = enabled && bounds !== null;
  let style: PopoverSizeStyle | null = null;
  if (enabled && bounds) {
    style = { maxWidth: bounds.maxWidth, maxHeight: bounds.maxHeight };
    if (pref.width !== undefined) style.width = clampSize(pref.width, bounds.minWidth, bounds.maxWidth);
    if (pref.height !== undefined) style.height = clampSize(pref.height, bounds.minHeight, bounds.maxHeight);
  }

  useLayoutEffect(() => {
    if (!open || !resizable) {
      setRendered(null);
      return;
    }
    const panel = panelRef.current;
    if (panel) setRendered({ width: panel.offsetWidth, height: panel.offsetHeight });
  }, [open, resizable, pref, bounds, panelRef]);

  return {
    style,
    resizable,
    custom: pref.width !== undefined || pref.height !== undefined,
    rendered,
    startResize,
    onResizeKey,
    reset,
  };
}
