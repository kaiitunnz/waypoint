"use client";

import { useRef, type KeyboardEvent, type PointerEvent } from "react";

import type { PopoverBounds } from "@waypoint/web-core/hooks/use-popover-anchor";
import type { ResizeAxis, ResizeDirection } from "@waypoint/web-core/popoverSize";

import { cx } from "@/lib/cx";

import { Icon } from "./Icon";
import styles from "./ResizeControls.module.css";

interface ResizeControlsProps {
  direction: ResizeDirection;
  bounds: PopoverBounds;
  rendered: { width: number; height: number } | null;
  // Whether a size of the person's own is in force.
  custom: boolean;
  onStart: (axis: ResizeAxis) => (event: PointerEvent) => void;
  onKey: (axis: ResizeAxis) => (event: KeyboardEvent) => void;
  onReset: () => void;
}

export function ResizeControls({ direction, bounds, rendered, custom, onStart, onKey, onReset }: ResizeControlsProps) {
  const widthSeam = useRef<HTMLDivElement>(null);
  const edgeX = direction.width < 0 ? styles.left : styles.right;
  const edgeY = direction.height < 0 ? styles.top : styles.bottom;

  const start = (axis: ResizeAxis) => (event: PointerEvent) => {
    // Holds the resize cursor while the pointer is away from the seam.
    event.currentTarget.setPointerCapture(event.pointerId);
    onStart(axis)(event);
  };

  return (
    <>
      <div
        ref={widthSeam}
        className={cx(styles.seam, styles.width, edgeX)}
        role="separator"
        aria-orientation="vertical"
        aria-label="Resize width"
        aria-valuenow={Math.round(rendered?.width ?? bounds.minWidth)}
        aria-valuemin={bounds.minWidth}
        aria-valuemax={bounds.maxWidth}
        tabIndex={0}
        onPointerDown={start("width")}
        onKeyDown={onKey("width")}
      />
      <div
        className={cx(styles.seam, styles.height, edgeY)}
        role="separator"
        aria-orientation="horizontal"
        aria-label="Resize height"
        aria-valuenow={Math.round(rendered?.height ?? bounds.minHeight)}
        aria-valuemin={bounds.minHeight}
        aria-valuemax={bounds.maxHeight}
        tabIndex={0}
        onPointerDown={start("height")}
        onKeyDown={onKey("height")}
      />
      {custom ? (
        <button
          type="button"
          className={cx(styles.reset, edgeX, edgeY)}
          aria-label="Reset popover size"
          title="Reset size"
          onClick={() => {
            onReset();
            // The button goes with the custom size; focus stays in the panel.
            widthSeam.current?.focus();
          }}
        >
          <Icon name="reset" size={12} />
        </button>
      ) : null}
    </>
  );
}
