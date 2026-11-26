import type { ITheme } from "@xterm/xterm";

import { buildExtendedAnsi, type TerminalAppearance } from "@waypoint/web-core/session/terminal";

const EXTENDED_ANSI = buildExtendedAnsi();

const ANSI = [
  "black",
  "red",
  "green",
  "yellow",
  "blue",
  "magenta",
  "cyan",
  "white",
  "bright-black",
  "bright-red",
  "bright-green",
  "bright-yellow",
  "bright-blue",
  "bright-magenta",
  "bright-cyan",
  "bright-white",
] as const;

const camel = (name: string) => name.replace(/-(\w)/g, (_, letter: string) => letter.toUpperCase());

// xterm paints with values, so the pane's colours are read from the tokens.
function readTheme(appearance: TerminalAppearance): ITheme {
  const style = getComputedStyle(document.documentElement);
  const token = (name: string) => style.getPropertyValue(`--term-${appearance}-${name}`).trim();
  const background = token("bg");
  return {
    background,
    foreground: token("fg"),
    cursor: token("cursor"),
    cursorAccent: background,
    selectionBackground: token("selection"),
    ...Object.fromEntries(ANSI.map((name) => [camel(name), token(name)])),
    extendedAnsi: EXTENDED_ANSI,
  };
}

const themes = new Map<TerminalAppearance, ITheme>();

export function terminalThemeFor(appearance: TerminalAppearance): ITheme {
  let theme = themes.get(appearance);
  if (!theme) {
    theme = readTheme(appearance);
    themes.set(appearance, theme);
  }
  return theme;
}

// The pane's font size in px, from the token for this input.
export function terminalFontSize(): number {
  return parseFloat(getComputedStyle(document.documentElement).getPropertyValue("--f-term")) || 13;
}
