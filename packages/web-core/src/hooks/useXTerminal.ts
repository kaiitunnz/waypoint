"use client";

import { useEffect, useRef, useState, type RefObject } from "react";

import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import { Terminal, type ITheme } from "@xterm/xterm";

import type { TerminalAppearance } from "../session/terminal";
import type { TerminalSink } from "../session/terminalConnection";

export interface XTerminalHandle extends TerminalSink {
  fit(): void;
  cols(): number;
  rows(): number;
  focus(): void;
  blur(): void;
  // Synchronous, so a focus() in the same tap lands on a writable textarea
  // and a phone raises its keyboard.
  setInputEnabled(enabled: boolean): void;
  scrollToBottom(): void;
}

export interface XTerminalOptions {
  readOnly?: boolean;
  // Off for a pane whose grid the server owns (claude_tty is pinned to a
  // fixed size and announces it): fitting would misalign the cell-positioned
  // stream, so the host scrolls instead. Read once, at mount.
  autoFit?: boolean;
  fontSize?: number;
  themeFor: (appearance: TerminalAppearance) => ITheme;
  appearance?: TerminalAppearance;
  onData?: (data: string) => void;
  onResize?: (size: { cols: number; rows: number }) => void;
  // Fires when the view enters or leaves the scrollback.
  onScrollChange?: (atBottom: boolean) => void;
}

function refitOnResize(host: HTMLElement, fit: FitAddon): () => void {
  const observer = new ResizeObserver(() => {
    try {
      fit.fit();
    } catch {
      // Detached mid-resize; the next tick retries.
    }
  });
  observer.observe(host);
  return () => observer.disconnect();
}

// Keeps a scrolling host on its bottom row as it shrinks, unless the person
// has scrolled up.
function pinToBottomOnResize(host: HTMLElement): () => void {
  let pinned = true;
  const onScroll = () => {
    pinned = host.scrollTop + host.clientHeight >= host.scrollHeight - 2;
  };
  host.addEventListener("scroll", onScroll, { passive: true });
  const observer = new ResizeObserver(() => {
    if (pinned) host.scrollTop = host.scrollHeight;
  });
  observer.observe(host);
  return () => {
    observer.disconnect();
    host.removeEventListener("scroll", onScroll);
  };
}

// OSC 52 is the terminal clipboard protocol Claude Code and Codex emit for
// /copy: `<targets>;<base64>`. A "?" payload is a read, which is refused so
// the page clipboard never reaches the wrapped CLI.
function copyOsc52(data: string): boolean {
  const sep = data.indexOf(";");
  if (sep < 0) return true;
  const payload = data.slice(sep + 1);
  if (payload === "?" || payload === "") return true;
  try {
    const bytes = Uint8Array.from(atob(payload), (c) => c.charCodeAt(0));
    // The write rejects without a user gesture; the CLI's own response file
    // is the fallback. The second `?.` matters: `.catch` on undefined throws.
    navigator.clipboard?.writeText(new TextDecoder().decode(bytes))?.catch(() => {});
  } catch {
    // Bad base64 or no clipboard API.
  }
  return true;
}

// An xterm.js pane in the element `host` points at. The app owns the element
// and its styling; the returned handle is stable.
export function useXTerminal(host: RefObject<HTMLElement | null>, options: XTerminalOptions): XTerminalHandle {
  const { readOnly = false, appearance = "dark", fontSize = 13 } = options;
  const term = useRef<Terminal | null>(null);
  const fitAddon = useRef<FitAddon | null>(null);
  const latest = useRef(options);
  const atBottom = useRef(true);
  useEffect(() => {
    latest.current = options;
  });

  useEffect(() => {
    const element = host.current;
    if (!element) return;
    const { autoFit = true, fontSize: initialFontSize = 13, themeFor } = latest.current;
    const terminal = new Terminal({
      fontFamily: '"JetBrains Mono", "SFMono-Regular", "Menlo", "Consolas", monospace',
      fontSize: initialFontSize,
      lineHeight: 1.25,
      // Every frame positions the cursor explicitly, so a still underline
      // reads as part of the TUI rather than the browser.
      cursorBlink: false,
      cursorStyle: "underline",
      theme: themeFor(latest.current.appearance ?? "dark"),
      scrollback: 20000,
      allowProposedApi: true,
      disableStdin: latest.current.readOnly ?? false,
      // On a fixed grid a mouse-tracking TUI captures plain drags, so ⌥-drag
      // selects on macOS (Shift-drag elsewhere), and ⌥-click must not type
      // cursor keys into the TUI.
      macOptionClickForcesSelection: !autoFit,
      altClickMovesCursor: autoFit,
    });
    const fit = autoFit ? new FitAddon() : null;
    if (fit) terminal.loadAddon(fit);
    terminal.loadAddon(new WebLinksAddon());
    terminal.open(element);

    // A fixed grid scrolls its host; xterm would take the wheel as cursor keys
    // or mouse reports and cancel it.
    const keepHostWheel = (event: WheelEvent) => event.stopPropagation();
    if (!autoFit) element.addEventListener("wheel", keepHostWheel, { capture: true, passive: true });

    // Subscribed before the first fit, so the fitted size reaches the caller.
    const dataSub = terminal.onData((data) => latest.current.onData?.(data));
    const resizeSub = terminal.onResize(({ cols, rows }) => latest.current.onResize?.({ cols, rows }));
    const scrollSub = terminal.onScroll(() => {
      const buffer = terminal.buffer.active;
      const bottom = buffer.viewportY >= buffer.baseY;
      if (bottom === atBottom.current) return;
      atBottom.current = bottom;
      latest.current.onScrollChange?.(bottom);
    });
    const oscSub = terminal.parser.registerOscHandler(52, copyOsc52);

    try {
      fit?.fit();
    } catch {
      // A zero-sized host; the observer below retries.
    }
    term.current = terminal;
    fitAddon.current = fit;
    latest.current.onResize?.({ cols: terminal.cols, rows: terminal.rows });
    const stopObserving = fit ? refitOnResize(element, fit) : pinToBottomOnResize(element);

    return () => {
      dataSub.dispose();
      resizeSub.dispose();
      scrollSub.dispose();
      oscSub.dispose();
      element.removeEventListener("wheel", keepHostWheel, { capture: true });
      stopObserving();
      terminal.dispose();
      term.current = null;
      fitAddon.current = null;
    };
  }, [host]);

  useEffect(() => {
    if (term.current) term.current.options.disableStdin = readOnly;
  }, [readOnly]);

  useEffect(() => {
    const terminal = term.current;
    if (!terminal || terminal.options.fontSize === fontSize) return;
    terminal.options.fontSize = fontSize;
    try {
      fitAddon.current?.fit();
    } catch {
      // See above.
    }
  }, [fontSize]);

  // xterm's DOM renderer bakes colours into painted cells, so a theme swap
  // repaints the visible rows.
  useEffect(() => {
    const terminal = term.current;
    if (!terminal) return;
    terminal.options.theme = latest.current.themeFor(appearance);
    terminal.refresh(0, terminal.rows - 1);
  }, [appearance]);

  const [handle] = useState<XTerminalHandle>(() => ({
      write: (data) => term.current?.write(data),
      reset: () => term.current?.reset(),
      fit: () => {
        try {
          fitAddon.current?.fit();
        } catch {
          // See above.
        }
      },
      // A fixed grid takes the server's size. It can exceed the viewport, so
      // the host lands on the bottom, where the live row is.
      resize: (cols, rows) => {
        const terminal = term.current;
        if (!terminal || cols <= 0 || rows <= 0) return;
        terminal.resize(cols, rows);
        const element = host.current;
        if (element) requestAnimationFrame(() => (element.scrollTop = element.scrollHeight));
      },
      cols: () => term.current?.cols ?? 80,
      rows: () => term.current?.rows ?? 24,
      dims: () => (term.current ? { cols: term.current.cols, rows: term.current.rows } : null),
      focus: () => term.current?.focus(),
      blur: () => term.current?.blur(),
      setInputEnabled: (enabled) => {
        if (term.current) term.current.options.disableStdin = !enabled;
      },
      scrollToBottom: () => term.current?.scrollToBottom(),
      setAppearance: (next) => {
        const terminal = term.current;
        if (!terminal) return;
        terminal.options.theme = latest.current.themeFor(next);
        terminal.refresh(0, terminal.rows - 1);
      },
  }));
  return handle;
}
