"use client";

import { useCallback, useEffect, useRef, type KeyboardEvent, type PointerEvent } from "react";

const REPEAT_DELAY_MS = 320;
const REPEAT_INTERVAL_MS = 70;

export interface HoldRepeatHandlers {
  onPointerDown: (event: PointerEvent<HTMLElement>) => void;
  onPointerUp: () => void;
  onPointerCancel: () => void;
  onKeyDown: (event: KeyboardEvent<HTMLElement>) => void;
  onContextMenu: (event: { preventDefault: () => void }) => void;
}

// Press-and-hold repeat for a button. `bind(value)` returns its handlers.
export function useHoldRepeat<T>(fire: (value: T) => void): (value: T) => HoldRepeatHandlers {
  const delay = useRef<number | null>(null);
  const repeat = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (delay.current !== null) window.clearTimeout(delay.current);
    if (repeat.current !== null) window.clearInterval(repeat.current);
    delay.current = null;
    repeat.current = null;
  }, []);

  useEffect(() => stop, [stop]);

  return useCallback(
    (value: T) => ({
      onPointerDown: (event) => {
        event.preventDefault();
        // A finger drifting off the target mid-hold keeps the repeat going.
        event.currentTarget.setPointerCapture(event.pointerId);
        stop();
        fire(value);
        delay.current = window.setTimeout(() => {
          repeat.current = window.setInterval(() => fire(value), REPEAT_INTERVAL_MS);
        }, REPEAT_DELAY_MS);
      },
      onPointerUp: stop,
      onPointerCancel: stop,
      // Pointer handlers miss keyboard activation; the OS key repeat re-fires
      // keydown while held. preventDefault keeps Space from paging.
      onKeyDown: (event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        event.preventDefault();
        fire(value);
      },
      onContextMenu: (event) => event.preventDefault(),
    }),
    [fire, stop],
  );
}
