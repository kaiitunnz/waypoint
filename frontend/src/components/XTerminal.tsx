"use client";

import { forwardRef, useEffect, useImperativeHandle, useRef } from "react";

import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { ITheme, Terminal } from "@xterm/xterm";
import "@xterm/xterm/css/xterm.css";

export interface XTerminalHandle {
  write(data: string): void;
  reset(): void;
  fit(): void;
  cols(): number;
  rows(): number;
  focus(): void;
  scrollToBottom(): void;
}

interface XTerminalProps {
  theme?: "dark" | "light";
  readOnly?: boolean;
  onData?: (data: string) => void;
  onResize?: (size: { cols: number; rows: number }) => void;
  // Fires whenever the user's distance from the live cursor changes. Used
  // by SessionDetail to surface a "jump to live" pill once the viewport
  // has slipped into the scrollback.
  onScrollChange?: (isAtBottom: boolean) => void;
  className?: string;
}

const DARK_THEME: ITheme = {
  background: "#060810",
  foreground: "#e7ecf3",
  cursor: "#e0bb73",
  cursorAccent: "#0a0d12",
  selectionBackground: "rgba(200, 169, 106, 0.30)",
  black: "#0a0d12",
  red: "#e26c70",
  green: "#6cc99a",
  yellow: "#d99a4a",
  blue: "#8fb3d6",
  magenta: "#c8a3eb",
  cyan: "#6cc4d6",
  white: "#e7ecf3",
  brightBlack: "#6f7a8c",
  brightRed: "#ff8a8e",
  brightGreen: "#8ee0b3",
  brightYellow: "#e0bb73",
  brightBlue: "#b0cfee",
  brightMagenta: "#dcc1f0",
  brightCyan: "#9adeec",
  brightWhite: "#f5f7fb",
};

// Build the 240-entry ``ITheme.extendedAnsi`` palette (indices 16–255)
// for light mode. The xterm 256-color palette has two halves:
//   • 16–231: a 6×6×6 RGB cube. Saturated cube cells (greens, blues,
//     oranges, …) read fine on either theme, so we keep the standard
//     values for almost the whole cube. The single exception is index
//     231 (pure ``#ffffff``) which TUIs use for emphasis text — on
//     cream it disappears, so we darken it to the default foreground.
//   • 232–255: a 24-step grayscale ramp. TUIs (claude_code uses
//     237/238 for the user-message highlight bar, 244/246 for muted
//     text) treat "dark gray index" as a subtle background highlight
//     and "light gray index" as muted/dim text. Both inversions
//     happen against the cream page: harsh dark bars and washed-out
//     pale text. Re-map the ramp to a *warm-cream* gradient that
//     inverts the lightness so CC's dark bg-indices become soft
//     near-cream tints, and CC's light fg-indices become readable
//     medium-warm grays.
function buildLightExtendedAnsi(): string[] {
  const palette: string[] = [];
  const cubeLevels = [0, 95, 135, 175, 215, 255];
  for (let r = 0; r < 6; r++) {
    for (let g = 0; g < 6; g++) {
      for (let b = 0; b < 6; b++) {
        palette.push(toHex(cubeLevels[r], cubeLevels[g], cubeLevels[b]));
      }
    }
  }
  // Override pure-white cube cell (index 231 in xterm, position 215 in
  // extendedAnsi) so emphasis text renders as the default near-black fg
  // instead of vanishing into the cream background.
  palette[215] = "#1c1914";
  // Inverted warm grayscale ramp: i=0 (xterm 232, originally darkest)
  // is the lightest warm shade; i=23 (xterm 255, originally lightest)
  // is the near-black warm shade.
  const rampStart = { r: 232, g: 223, b: 200 };
  const rampEnd = { r: 28, g: 25, b: 20 };
  for (let i = 0; i < 24; i++) {
    const t = i / 23;
    const r = Math.round(rampStart.r * (1 - t) + rampEnd.r * t);
    const g = Math.round(rampStart.g * (1 - t) + rampEnd.g * t);
    const b = Math.round(rampStart.b * (1 - t) + rampEnd.b * t);
    palette.push(toHex(r, g, b));
  }
  return palette;
}

function toHex(r: number, g: number, b: number): string {
  return "#" + [r, g, b].map((v) => v.toString(16).padStart(2, "0")).join("");
}

const LIGHT_EXTENDED_ANSI = buildLightExtendedAnsi();

const LIGHT_THEME: ITheme = {
  background: "#f7f3eb",
  foreground: "#1c1914",
  cursor: "#b8820e",
  cursorAccent: "#fffdf7",
  selectionBackground: "rgba(184, 130, 14, 0.22)",
  black: "#1c1914",
  red: "#b83038",
  green: "#257a50",
  yellow: "#aa6618",
  blue: "#2f6f9e",
  magenta: "#7040b0",
  cyan: "#1e7a8e",
  white: "#6e6356",
  brightBlack: "#a09380",
  brightRed: "#d04050",
  brightGreen: "#2e9866",
  brightYellow: "#c87a18",
  brightBlue: "#3a87ba",
  brightMagenta: "#8c52cc",
  brightCyan: "#2c98b0",
  brightWhite: "#0e0c09",
  extendedAnsi: LIGHT_EXTENDED_ANSI,
};

function themeFor(mode: "dark" | "light"): ITheme {
  return mode === "light" ? LIGHT_THEME : DARK_THEME;
}

export const XTerminal = forwardRef<XTerminalHandle, XTerminalProps>(
  function XTerminal(
    { theme = "dark", readOnly = false, onData, onResize, onScrollChange, className },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const termRef = useRef<Terminal | null>(null);
    const fitRef = useRef<FitAddon | null>(null);
    const onDataRef = useRef(onData);
    const onResizeRef = useRef(onResize);
    const onScrollChangeRef = useRef(onScrollChange);
    const wasAtBottomRef = useRef(true);

    useEffect(() => {
      onDataRef.current = onData;
    }, [onData]);
    useEffect(() => {
      onResizeRef.current = onResize;
    }, [onResize]);
    useEffect(() => {
      onScrollChangeRef.current = onScrollChange;
    }, [onScrollChange]);

    useEffect(() => {
      const host = containerRef.current;
      if (!host) return;
      const term = new Terminal({
        fontFamily:
          '"JetBrains Mono", "SFMono-Regular", "Menlo", "Consolas", monospace',
        fontSize: 13,
        lineHeight: 1.25,
        // The pane stream emits cell-level deltas with explicit CUP
        // positioning for the cursor on every frame, so we never rely
        // on xterm's own blink timer to advertise where the cursor is.
        // A still underline reads as part of the TUI rather than the
        // browser.
        cursorBlink: false,
        cursorStyle: "underline",
        scrollback: 20000,
        allowProposedApi: true,
        disableStdin: readOnly,
        theme: themeFor(theme),
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.loadAddon(new WebLinksAddon());
      term.open(host);

      // Subscribe before the first fit so the initial dimension change
      // (default 80x24 → fitted size) actually reaches the parent. Then
      // push the current dims unconditionally after setup; if fit() was
      // a no-op (already at target size or threw on a 0-sized host) we
      // still report what the terminal believes its size is.
      const onDataSub = term.onData((data) => {
        onDataRef.current?.(data);
      });
      const onResizeSub = term.onResize(({ cols, rows }) => {
        onResizeRef.current?.({ cols, rows });
      });
      // ``viewportY < baseY`` means the user is reading scrollback. Edge-
      // trigger the callback only on transitions to avoid spamming React
      // state on every scroll-tick.
      const emitScrollState = () => {
        const buf = term.buffer.active;
        const atBottom = buf.viewportY >= buf.baseY;
        if (atBottom !== wasAtBottomRef.current) {
          wasAtBottomRef.current = atBottom;
          onScrollChangeRef.current?.(atBottom);
        }
      };
      const onScrollSub = term.onScroll(emitScrollState);

      // Initial fit can throw if the container is still 0-sized (animation,
      // hidden tab, etc.) — guard so we don't crash the React tree.
      try {
        fit.fit();
      } catch {
        // ResizeObserver below will retry once the container has a size.
      }
      termRef.current = term;
      fitRef.current = fit;
      onResizeRef.current?.({ cols: term.cols, rows: term.rows });

      const ro = new ResizeObserver(() => {
        try {
          fit.fit();
        } catch {
          // Container detached mid-resize; ignore until next tick.
        }
      });
      ro.observe(host);

      return () => {
        onDataSub.dispose();
        onResizeSub.dispose();
        onScrollSub.dispose();
        ro.disconnect();
        term.dispose();
        termRef.current = null;
        fitRef.current = null;
      };
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
      const term = termRef.current;
      if (!term) return;
      term.options.theme = themeFor(theme);
    }, [theme]);

    useEffect(() => {
      const term = termRef.current;
      if (!term) return;
      term.options.disableStdin = readOnly;
    }, [readOnly]);

    useImperativeHandle(ref, () => ({
      write: (data: string) => {
        termRef.current?.write(data);
      },
      reset: () => {
        termRef.current?.reset();
      },
      fit: () => {
        try {
          fitRef.current?.fit();
        } catch {
          // see above
        }
      },
      cols: () => termRef.current?.cols ?? 80,
      rows: () => termRef.current?.rows ?? 24,
      focus: () => termRef.current?.focus(),
      scrollToBottom: () => {
        termRef.current?.scrollToBottom();
      },
    }));

    return (
      <div
        ref={containerRef}
        className={className ?? "xterm-host"}
        style={{ width: "100%", height: "100%" }}
      />
    );
  },
);

export default XTerminal;
