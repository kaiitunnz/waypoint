"use client";

import { useHoldRepeat } from "@waypoint/web-core/hooks/useHoldRepeat";
import type { ScrollDirection } from "@waypoint/web-core/session/terminal";

interface TerminalScrollChipsProps {
  onWheel: (direction: ScrollDirection) => void;
  // When true, the cluster moves up to make room for the "Jump to live"
  // pill sharing the bottom-right corner.
  withJump?: boolean;
}

export function TerminalScrollChips({ onWheel, withJump }: TerminalScrollChipsProps) {
  const hold = useHoldRepeat(onWheel);

  return (
    <div
      className="term-scroll-cluster"
      data-with-jump={withJump ? "true" : undefined}
      role="group"
      aria-label="Terminal scroll"
    >
      <button type="button" {...hold("up")} aria-label="Scroll up">
        ↑
      </button>
      <button type="button" {...hold("down")} aria-label="Scroll down">
        ↓
      </button>
    </div>
  );
}

export default TerminalScrollChips;
