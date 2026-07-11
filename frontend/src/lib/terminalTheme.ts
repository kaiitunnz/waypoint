import { ITheme } from "@xterm/xterm";

// The light/dark surface a terminal pane adopts, resolved from the running
// agent's own theme preference and delivered over the terminal websocket. This
// is independent of Waypoint's app theme toggle: the pane hosts an opaque TUI,
// so it follows the TUI's ground, not the web chrome's.
export type TerminalAppearance = "light" | "dark";

// The fixed xterm 256-colour palette for indices 16-255 (the 6×6×6 colour cube
// then the 24-step grayscale ramp). These indices carry no light/dark
// semantics — they are absolute colours a TUI addresses directly — so both
// themes share them, and we set them explicitly rather than leaning on xterm's
// renderer-specific defaults.
function buildExtendedAnsi(): string[] {
  const hex = (n: number) => n.toString(16).padStart(2, "0");
  const rgb = (r: number, g: number, b: number) => `#${hex(r)}${hex(g)}${hex(b)}`;
  const levels = [0, 95, 135, 175, 215, 255];
  const out: string[] = [];
  for (let r = 0; r < 6; r++) {
    for (let g = 0; g < 6; g++) {
      for (let b = 0; b < 6; b++) {
        out.push(rgb(levels[r], levels[g], levels[b]));
      }
    }
  }
  for (let i = 0; i < 24; i++) {
    const v = 8 + i * 10;
    out.push(rgb(v, v, v));
  }
  return out;
}

const EXTENDED_ANSI = buildExtendedAnsi();

// The dark surface — the historical Waypoint terminal look. TUIs emit truecolor
// output tuned for a dark ground (e.g. Claude Code's diff backgrounds), so this
// remains the safe default and the fallback whenever appearance is unknown.
export const DARK_TERMINAL_THEME: ITheme = {
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
  extendedAnsi: EXTENDED_ANSI,
};

// The light surface — a warm off-white ground with dark default text, matching
// a native light TUI theme. The 16 base slots are darkened/saturated for
// contrast on the light ground; crucially the white/brightWhite slots resolve
// to dark values so a TUI's white-on-default emphasis stays readable (light
// text on a light ground would vanish). Truecolor cells the TUI emits are left
// untouched — only the palette and surface change.
export const LIGHT_TERMINAL_THEME: ITheme = {
  background: "#faf7f0",
  foreground: "#1c1914",
  cursor: "#b8820e",
  cursorAccent: "#faf7f0",
  selectionBackground: "rgba(184, 130, 14, 0.25)",
  black: "#2b2b2b",
  red: "#c0392b",
  green: "#2f8f4e",
  yellow: "#a6791f",
  blue: "#2d6bb8",
  magenta: "#9b4dca",
  cyan: "#1e8a9e",
  white: "#4a453b",
  brightBlack: "#6c665a",
  brightRed: "#d0503f",
  brightGreen: "#3aa860",
  brightYellow: "#b8820e",
  brightBlue: "#3a87ba",
  brightMagenta: "#b56fd8",
  brightCyan: "#2aa5bb",
  brightWhite: "#1c1914",
  extendedAnsi: EXTENDED_ANSI,
};

export function terminalThemeFor(appearance: TerminalAppearance): ITheme {
  return appearance === "light" ? LIGHT_TERMINAL_THEME : DARK_TERMINAL_THEME;
}
