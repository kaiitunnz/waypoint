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
}

interface XTerminalProps {
  theme?: "dark" | "light";
  readOnly?: boolean;
  onData?: (data: string) => void;
  onResize?: (size: { cols: number; rows: number }) => void;
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
};

function themeFor(mode: "dark" | "light"): ITheme {
  return mode === "light" ? LIGHT_THEME : DARK_THEME;
}

export const XTerminal = forwardRef<XTerminalHandle, XTerminalProps>(
  function XTerminal(
    { theme = "dark", readOnly = false, onData, onResize, className },
    ref,
  ) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const termRef = useRef<Terminal | null>(null);
    const fitRef = useRef<FitAddon | null>(null);
    const onDataRef = useRef(onData);
    const onResizeRef = useRef(onResize);

    useEffect(() => {
      onDataRef.current = onData;
    }, [onData]);
    useEffect(() => {
      onResizeRef.current = onResize;
    }, [onResize]);

    useEffect(() => {
      const host = containerRef.current;
      if (!host) return;
      const term = new Terminal({
        fontFamily:
          '"JetBrains Mono", "SFMono-Regular", "Menlo", "Consolas", monospace',
        fontSize: 13,
        lineHeight: 1.25,
        cursorBlink: !readOnly,
        cursorStyle: readOnly ? "underline" : "block",
        scrollback: 20000,
        convertEol: false,
        allowProposedApi: true,
        disableStdin: readOnly,
        theme: themeFor(theme),
      });
      const fit = new FitAddon();
      term.loadAddon(fit);
      term.loadAddon(new WebLinksAddon());
      term.open(host);
      // Initial fit can throw if the container is still 0-sized (animation,
      // hidden tab, etc.) — guard so we don't crash the React tree.
      try {
        fit.fit();
      } catch {
        // ResizeObserver below will retry once the container has a size.
      }
      termRef.current = term;
      fitRef.current = fit;

      const onDataSub = term.onData((data) => {
        onDataRef.current?.(data);
      });
      const onResizeSub = term.onResize(({ cols, rows }) => {
        onResizeRef.current?.({ cols, rows });
      });

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
