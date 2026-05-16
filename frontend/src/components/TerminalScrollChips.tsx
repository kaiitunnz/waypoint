"use client";

import { useCallback, useEffect, useRef } from "react";

interface TerminalScrollChipsProps {
  onWheel: (direction: "up" | "down") => void;
}

// First tap fires immediately; hold past REPEAT_DELAY then repeat at
// REPEAT_INTERVAL until release. Tuned so a single deliberate tap is
// one scroll tick, and a thumb-rest fast-scrolls without the user
// rate-tapping.
const REPEAT_DELAY_MS = 320;
const REPEAT_INTERVAL_MS = 70;

export function TerminalScrollChips({ onWheel }: TerminalScrollChipsProps) {
  const delayRef = useRef<number | null>(null);
  const repeatRef = useRef<number | null>(null);

  const stop = useCallback(() => {
    if (delayRef.current !== null) {
      window.clearTimeout(delayRef.current);
      delayRef.current = null;
    }
    if (repeatRef.current !== null) {
      window.clearInterval(repeatRef.current);
      repeatRef.current = null;
    }
  }, []);

  const start = useCallback(
    (direction: "up" | "down") => {
      stop();
      onWheel(direction);
      delayRef.current = window.setTimeout(() => {
        repeatRef.current = window.setInterval(() => {
          onWheel(direction);
        }, REPEAT_INTERVAL_MS);
      }, REPEAT_DELAY_MS);
    },
    [onWheel, stop],
  );

  useEffect(() => stop, [stop]);

  return (
    <div
      className="terminal-scroll-chips"
      role="group"
      aria-label="Terminal scroll"
    >
      <button
        type="button"
        className="terminal-scroll-chip"
        onPointerDown={(e) => {
          e.preventDefault();
          start("up");
        }}
        onPointerUp={stop}
        onPointerLeave={stop}
        onPointerCancel={stop}
        onContextMenu={(e) => e.preventDefault()}
        aria-label="Scroll up"
      >
        ↑
      </button>
      <button
        type="button"
        className="terminal-scroll-chip"
        onPointerDown={(e) => {
          e.preventDefault();
          start("down");
        }}
        onPointerUp={stop}
        onPointerLeave={stop}
        onPointerCancel={stop}
        onContextMenu={(e) => e.preventDefault()}
        aria-label="Scroll down"
      >
        ↓
      </button>
    </div>
  );
}

export default TerminalScrollChips;
