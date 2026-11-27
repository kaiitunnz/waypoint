"use client";

import { useCallback, useEffect, useRef, type MouseEvent, type PointerEvent, type TouchEvent } from "react";

import { HOLD_MS } from "./useHoldMenu";

// A finger that drifts further than this is scrolling, not pressing.
const SLOP_PX = 10;

// A held touch on an element. Its release does nothing, even on something the press
// just opened over the element; a tap is left alone.
export function useLongPress(onLongPress: (element: Element) => void) {
  const timer = useRef<number | null>(null);
  const origin = useRef<{ x: number; y: number } | null>(null);
  const pressed = useRef(false);

  const cancel = useCallback(() => {
    if (timer.current !== null) window.clearTimeout(timer.current);
    timer.current = null;
  }, []);
  useEffect(() => cancel, [cancel]);

  return {
    onPointerDown: (event: PointerEvent) => {
      pressed.current = false;
      if (event.pointerType !== "touch") return;
      cancel();
      origin.current = { x: event.clientX, y: event.clientY };
      const element = event.currentTarget;
      timer.current = window.setTimeout(() => {
        timer.current = null;
        pressed.current = true;
        onLongPress(element);
      }, HOLD_MS);
    },
    onPointerMove: (event: PointerEvent) => {
      const from = origin.current;
      if (timer.current !== null && from && Math.hypot(event.clientX - from.x, event.clientY - from.y) > SLOP_PX) cancel();
    },
    onPointerUp: cancel,
    // A scroll that takes the touch over cancels the pointer.
    onPointerCancel: cancel,
    // Refusing the release stops its mouse events and click, which would otherwise
    // land on a sheet opened under the finger.
    onTouchEnd: (event: TouchEvent) => {
      if (!pressed.current) return;
      pressed.current = false;
      if (event.cancelable) event.preventDefault();
    },
    onClickCapture: (event: MouseEvent) => {
      if (!pressed.current) return;
      pressed.current = false;
      event.preventDefault();
      event.stopPropagation();
    },
    // A held touch would open the system's menu.
    onContextMenu: (event: MouseEvent) => {
      if (pressed.current || timer.current !== null) event.preventDefault();
    },
  };
}
