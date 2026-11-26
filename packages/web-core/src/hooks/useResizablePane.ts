"use client";

import { useCallback, useEffect, useState, type KeyboardEvent, type PointerEvent as ReactPointerEvent } from "react";

import { clampPaneWidth, PANE_KEY_STEP, snapPaneWidth, type PaneWidthConfig } from "../paneResize";

const viewportWidth = () => (typeof window === "undefined" ? undefined : window.innerWidth);

export interface ResizablePane {
  // Undefined until the person picks a width.
  width: number | undefined;
  // The width in force.
  rendered: number;
  startDrag: (event: ReactPointerEvent) => void;
  onKeyDown: (event: KeyboardEvent) => void;
}

// A seam the person drags, or moves with the arrow keys; the width is remembered.
// `config` and `onDragChange` must be stable.
export function useResizablePane(
  config: PaneWidthConfig,
  onDragChange?: (dragging: boolean) => void,
): ResizablePane {
  const [width, setWidth] = useState<number | undefined>(() => {
    if (typeof window === "undefined") return undefined;
    const stored = Number(window.localStorage.getItem(config.storageKey));
    return Number.isFinite(stored) && stored > 0 ? clampPaneWidth(stored, config, viewportWidth()) : undefined;
  });

  useEffect(() => {
    if (typeof window === "undefined") return;
    if (width === undefined) {
      window.localStorage.removeItem(config.storageKey);
    } else {
      window.localStorage.setItem(config.storageKey, String(width));
    }
  }, [width, config]);

  const startDrag = useCallback(
    (event: ReactPointerEvent) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = width ?? config.defaultWidth;
      const onMove = (move: PointerEvent) =>
        setWidth(snapPaneWidth(startWidth + (move.clientX - startX), config, viewportWidth()));
      const onUp = () => {
        window.removeEventListener("pointermove", onMove);
        window.removeEventListener("pointerup", onUp);
        onDragChange?.(false);
      };
      onDragChange?.(true);
      window.addEventListener("pointermove", onMove);
      window.addEventListener("pointerup", onUp);
    },
    [width, config, onDragChange],
  );

  const onKeyDown = useCallback(
    (event: KeyboardEvent) => {
      const step = event.key === "ArrowRight" ? PANE_KEY_STEP : event.key === "ArrowLeft" ? -PANE_KEY_STEP : 0;
      if (!step) return;
      event.preventDefault();
      setWidth((current) => snapPaneWidth((current ?? config.defaultWidth) + step, config, viewportWidth()));
    },
    [config],
  );

  return { width, rendered: width ?? config.defaultWidth, startDrag, onKeyDown };
}
