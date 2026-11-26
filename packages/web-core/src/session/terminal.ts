// The light/dark surface a terminal pane adopts, resolved from the running
// agent's own TUI theme and delivered over the terminal websocket. It is
// independent of the app theme: the pane hosts an opaque TUI, so it follows the
// TUI's ground, not the web chrome's.
export type TerminalAppearance = "light" | "dark";

export interface TerminalDims {
  cols: number;
  rows: number;
}

export type TerminalKeyGroup = "edit" | "arrows" | "clipboard";

// A key-bar entry sends literal bytes to the pane, or asks the host to paste.
export type TerminalKey = {
  label: string;
  title: string;
  group: TerminalKeyGroup;
  // The agents that bind the key on their main surface. Advisory: a client
  // may show the key everywhere.
  backends?: readonly string[];
} & ({ data: string } | { action: "paste" });

// Control bytes that are awkward to type on touch keyboards but routine for
// TUI agents. Ctrl combinations are encoded directly so the bar works when
// the OS keyboard has no Ctrl modifier.
export const TERMINAL_KEYS: readonly TerminalKey[] = [
  { label: "Esc", data: "\x1b", title: "Escape", group: "edit" },
  { label: "Tab", data: "\t", title: "Tab", group: "edit" },
  // Claude Code and the Codex TUI cycle modes on Shift-Tab.
  { label: "⇧Tab", data: "\x1b[Z", title: "Shift-Tab (cycle modes)", group: "edit", backends: ["claude_code", "codex"] },
  { label: "Enter", data: "\r", title: "Enter (submit / select)", group: "edit" },
  // Ctrl-J: the agents' soft newline, where Enter submits.
  { label: "⇧↵", data: "\n", title: "Shift+Enter (newline)", group: "edit" },
  { label: "↑", data: "\x1b[A", title: "Up", group: "arrows" },
  { label: "↓", data: "\x1b[B", title: "Down", group: "arrows" },
  { label: "←", data: "\x1b[D", title: "Left", group: "arrows" },
  { label: "→", data: "\x1b[C", title: "Right", group: "arrows" },
  { label: "Paste", action: "paste", title: "Paste from clipboard", group: "clipboard" },
  { label: "^C", data: "\x03", title: "Ctrl-C (interrupt)", group: "clipboard" },
];

export type ScrollDirection = "up" | "down";

// An SGR (mode 1006) wheel report, so the TUI inside the pane scrolls its own
// view. Attributed to the grid's centre, a stand-in for where the eye is.
export function wheelSequence(direction: ScrollDirection, dims: TerminalDims | null): string {
  const cols = dims?.cols ?? 80;
  const rows = dims?.rows ?? 24;
  const col = Math.max(1, Math.floor(cols / 2));
  const row = Math.max(1, Math.floor(rows / 2));
  const button = direction === "up" ? 64 : 65;
  return `\x1b[<${button};${col};${row}M`;
}

export interface TerminalHello {
  type: "hello";
  terminal_protocol: 2;
  cols?: number;
  rows?: number;
  interactive?: true;
}

// Protocol v2 opens with a hello so the server sends the appearance frame.
// Resizable panes carry their viewport so tmux resizes to match; fixed grids
// own their geometry server-side. `interactive` asks a locked-by-default pane
// to accept typing.
export function terminalHello(dims: TerminalDims | null, interactive: boolean): TerminalHello {
  return {
    type: "hello",
    terminal_protocol: 2,
    ...(dims && dims.cols && dims.rows ? { cols: dims.cols, rows: dims.rows } : {}),
    ...(interactive ? { interactive: true as const } : {}),
  };
}

export type TerminalFrame =
  | { kind: "size"; cols: number; rows: number }
  | { kind: "appearance"; appearance: TerminalAppearance }
  | { kind: "output"; text: string };

// Output is always wrapped in a sync-update escape, so it begins with ESC; a
// leading "{" marks an out-of-band control frame.
export function parseTerminalFrame(data: string): TerminalFrame {
  if (data.charCodeAt(0) === 0x7b) {
    try {
      const msg = JSON.parse(data);
      if (msg?.type === "size" && typeof msg.cols === "number" && typeof msg.rows === "number") {
        return { kind: "size", cols: msg.cols, rows: msg.rows };
      }
      if (msg?.type === "appearance" && (msg.appearance === "light" || msg.appearance === "dark")) {
        return { kind: "appearance", appearance: msg.appearance };
      }
    } catch {
      // Not a control frame after all.
    }
  }
  return { kind: "output", text: data };
}

// WebKit (desktop Safari and every iOS browser) gates clipboard reads behind a
// system "Paste" banner, so pasted text is staged behind a tap to send.
export function isSafariFamily(userAgent: string, maxTouchPoints: number): boolean {
  const iOS = /\b(iPad|iPhone|iPod)\b/.test(userAgent) || (userAgent.includes("Mac") && maxTouchPoints > 1);
  if (iOS) return true;
  return /Safari\//.test(userAgent) && !/Chrome|Chromium|Edg\/|OPR\//.test(userAgent);
}

export function pasteNeedsConfirmation(): boolean {
  if (typeof navigator === "undefined") return false;
  return isSafariFamily(navigator.userAgent, navigator.maxTouchPoints ?? 0);
}

// The fixed xterm palette for indices 16-255: the 6×6×6 colour cube, then the
// 24-step grey ramp. Absolute colours, the same on light and dark surfaces.
export function buildExtendedAnsi(): string[] {
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  const rgb = (r: number, g: number, b: number) => `#${hex(r)}${hex(g)}${hex(b)}`;
  const levels = [0, 95, 135, 175, 215, 255];
  const out: string[] = [];
  for (const r of levels) {
    for (const g of levels) {
      for (const b of levels) out.push(rgb(r, g, b));
    }
  }
  for (let i = 0; i < 24; i++) {
    const v = 8 + i * 10;
    out.push(rgb(v, v, v));
  }
  return out;
}
