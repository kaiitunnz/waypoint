"use client";

import { forwardRef, useImperativeHandle, useRef } from "react";

import "@xterm/xterm/css/xterm.css";

import { useXTerminal, type XTerminalHandle } from "@waypoint/web-core/hooks/useXTerminal";
import type { TerminalAppearance } from "@waypoint/web-core/session/terminal";

import { terminalThemeFor } from "@/lib/terminalTheme";

export type { XTerminalHandle };

interface XTerminalProps {
  readOnly?: boolean;
  // When false the server owns the grid (claude_tty) and the parent applies it
  // through ``resize()``; the host scrolls. Fixed at mount.
  autoFit?: boolean;
  onData?: (data: string) => void;
  onResize?: (size: { cols: number; rows: number }) => void;
  // Surfaces the "jump to live" pill once the view slips into scrollback.
  onScrollChange?: (isAtBottom: boolean) => void;
  // Dark until the connection resolves the agent's theme.
  appearance?: TerminalAppearance;
  className?: string;
}

export const XTerminal = forwardRef<XTerminalHandle, XTerminalProps>(function XTerminal(
  { readOnly = false, autoFit = true, onData, onResize, onScrollChange, appearance = "dark", className },
  ref,
) {
  const containerRef = useRef<HTMLDivElement | null>(null);
  const handle = useXTerminal(containerRef, {
    readOnly,
    autoFit,
    themeFor: terminalThemeFor,
    appearance,
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
