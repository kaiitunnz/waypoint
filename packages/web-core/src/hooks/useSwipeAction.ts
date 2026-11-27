"use client";

import { useRef, useState, type PointerEvent } from "react";

// Movement before a drag is told apart from a tap.
const SLOP = 8;
// Releasing past this leftward distance acts.
const SWIPE_THRESHOLD = 96;

// A left swipe (touch or mouse drag) on a row. Vertical drags are left to the
// list's scrolling, so the row takes `touch-action: pan-y`.
export function useSwipeAction({ disabled = false, onAction }: { disabled?: boolean; onAction: () => void }) {
  const [offset, setOffset] = useState(0);
  const [swiping, setSwiping] = useState(false);
  const start = useRef<{ x: number; y: number } | null>(null);
  const axis = useRef<"none" | "horiz" | "vert">("none");
  const offsetRef = useRef(0);
  const dragged = useRef(false);

  function move(value: number) {
    offsetRef.current = value;
    setOffset(value);
  }

  function onPointerDown(event: PointerEvent<Element>) {
    if (disabled) return;
    if (event.pointerType === "mouse" && event.button !== 0) return;
    start.current = { x: event.clientX, y: event.clientY };
    axis.current = "none";
    dragged.current = false;
  }

  function onPointerMove(event: PointerEvent<Element>) {
    if (!start.current) return;
    const dx = event.clientX - start.current.x;
    const dy = event.clientY - start.current.y;
    if (axis.current === "none") {
      if (Math.abs(dx) < SLOP && Math.abs(dy) < SLOP) return;
      axis.current = Math.abs(dx) > Math.abs(dy) ? "horiz" : "vert";
      if (axis.current === "horiz") {
        setSwiping(true);
        event.currentTarget.setPointerCapture?.(event.pointerId);
      }
    }
    if (axis.current !== "horiz") return;
    dragged.current = true;
    move(Math.max(-SWIPE_THRESHOLD * 1.6, Math.min(0, dx)));
  }

  function finish() {
    if (axis.current === "horiz") {
      setSwiping(false);
      const passed = Math.abs(offsetRef.current) >= SWIPE_THRESHOLD;
      move(0);
      if (passed) onAction();
    }
    start.current = null;
    axis.current = "none";
  }

  // True when the click that follows a drag should be ignored.
  function consumeClick(): boolean {
    if (!dragged.current) return false;
    dragged.current = false;
    return true;
  }

  return {
    offset,
    swiping,
    consumeClick,
    handlers: { onPointerDown, onPointerMove, onPointerUp: finish, onPointerCancel: finish },
  };
}
