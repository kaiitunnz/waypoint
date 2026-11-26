import { ITheme } from "@xterm/xterm";

import { buildExtendedAnsi, type TerminalAppearance } from "@waypoint/web-core/session/terminal";

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
// contrast on the light ground, but the grayscale slots keep their native
// light-terminal luminance ordering: black darkest, white a light gray,
// brightWhite the lightest. An ANSI-only TUI theme addresses these slots as
// *surfaces* as well as text — e.g. Claude's light-ansi theme paints the
// user-message bar with `ansi:white` behind `ansi:black` text — so white must
// stay light for that bar to read as a light panel with legible dark text.
// Truecolor cells the TUI emits are left untouched — only the palette and
// surface change.
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
  white: "#d0cabb",
  brightBlack: "#6c665a",
  brightRed: "#d0503f",
  brightGreen: "#3aa860",
  brightYellow: "#b8820e",
  brightBlue: "#3a87ba",
  brightMagenta: "#b56fd8",
  brightCyan: "#2aa5bb",
  brightWhite: "#efe9dc",
  extendedAnsi: EXTENDED_ANSI,
};

export function terminalThemeFor(appearance: TerminalAppearance): ITheme {
  return appearance === "light" ? LIGHT_TERMINAL_THEME : DARK_TERMINAL_THEME;
}
