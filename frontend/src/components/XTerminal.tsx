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
  resize(cols: number, rows: number): void;
  cols(): number;
  rows(): number;
  focus(): void;
  scrollToBottom(): void;
}

interface XTerminalProps {
  theme?: "dark" | "light";
  readOnly?: boolean;
  // When false, the terminal does not fit its grid to the container. The
  // server owns the geometry (emulated panes like claude_tty are pinned to a
  // fixed size) and drives the grid via a ``CSI 8 ; rows ; cols t`` resize, so
  // fitting here would override it and misalign the cell-positioned stream.
  // The host scrolls instead. Defaults to true (resizable tmux behavior).
  autoFit?: boolean;
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
    {
      theme = "dark",
      readOnly = false,
      autoFit = true,
      onData,
      onResize,
      onScrollChange,
      className,
    },
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
      const fit = autoFit ? new FitAddon() : null;
      if (fit) term.loadAddon(fit);
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

      // OSC 52 is the terminal-clipboard protocol Claude Code (and Codex
      // TUI) emit for `/copy`. Format: ``\x1b]52;<targets>;<payload>\x07``
      // where ``<targets>`` is one or more of c (clipboard), p (primary),
      // q (secondary), s (selection), 0–7 (cut buffers), and ``<payload>``
      // is base64 (or "?" for a read query, which we ignore for security).
      // Without this handler xterm.js drops the sequence silently and the
      // user's `/copy` looks broken even though the CLI did its job.
      const osc52Sub = term.parser.registerOscHandler(52, (data) => {
        const sep = data.indexOf(";");
        if (sep < 0) return true;
        const payload = data.slice(sep + 1);
        // "?" is a clipboard read; we deliberately don't surface page
        // clipboard contents back to the wrapped CLI.
        if (payload === "?" || payload === "") return true;
        try {
          const binary = atob(payload);
          const bytes = Uint8Array.from(binary, (c) => c.charCodeAt(0));
          const text = new TextDecoder().decode(bytes);
          navigator.clipboard?.writeText(text)?.catch(() => {
            // Async write can reject when the clipboard API is gated
            // (no transient activation, insecure context, missing
            // permission). The CLI's /tmp/claude-<uid>/response.md is
            // the documented fallback in those cases. The second ``?.``
            // is load-bearing: without it, ``.catch`` runs on the
            // optional chain's ``undefined`` result and throws.
          });
        } catch {
          // Bad base64, missing clipboard API, or insecure context — silent
          // fallback; the CLI also wrote /tmp/claude-<uid>/response.md for
          // exactly this case.
        }
        return true;
      });

      // Initial fit can throw if the container is still 0-sized (animation,
      // hidden tab, etc.) — guard so we don't crash the React tree.
      try {
        fit?.fit();
      } catch {
        // ResizeObserver below will retry once the container has a size.
      }
      termRef.current = term;
      fitRef.current = fit;
      onResizeRef.current?.({ cols: term.cols, rows: term.rows });

      // Only resizable panes refit on container changes. Fixed-grid panes
      // keep the server-driven grid and let the host scroll instead.
      const ro = fit
        ? new ResizeObserver(() => {
            try {
              fit.fit();
            } catch {
              // Container detached mid-resize; ignore until next tick.
            }
          })
        : null;
      ro?.observe(host);

      return () => {
        onDataSub.dispose();
        onResizeSub.dispose();
        onScrollSub.dispose();
        osc52Sub.dispose();
        ro?.disconnect();
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
      // Used for fixed-grid panes: the server dictates the grid size and we
      // apply it directly rather than fitting to the container.
      resize: (cols: number, rows: number) => {
        if (cols > 0 && rows > 0) termRef.current?.resize(cols, rows);
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
