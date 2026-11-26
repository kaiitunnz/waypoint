"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";

import "@xterm/xterm/css/xterm.css";

import { useXTerminal, type XTerminalHandle } from "@waypoint/web-core/hooks/useXTerminal";

import { terminalThemeFor } from "@/lib/terminalTheme";

export type { XTerminalHandle };

interface XTerminalProps {
  readOnly?: boolean;
  autoFit?: boolean;
  onData?: (data: string) => void;
  onResize?: (size: { cols: number; rows: number }) => void;
  onScrollChange?: (isAtBottom: boolean) => void;
  className?: string;
}

export const XTerminal = forwardRef<XTerminalHandle, XTerminalProps>(function XTerminal(
  { readOnly = false, autoFit = true, onData, onResize, onScrollChange, className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const handle = useXTerminal(containerRef, {
    readOnly,
    autoFit,
    themeFor: terminalThemeFor,
    onData,
    onResize,
    onScrollChange,
  });
  useImperativeHandle(ref, () => handle, [handle]);

  return (
    <div
      ref={containerRef}
      // A fixed grid can exceed the pane, so its host scrolls; a resizable one
      // must not, so mouse events reach the backend.
      className={`${className ?? "xterm-host"}${autoFit ? "" : " xterm-host--scroll"}`}
      style={{ width: "100%", height: "100%" }}
    />
  );
});

export default XTerminal;
