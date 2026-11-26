import { describe, expect, it } from "vitest";

import {
  buildExtendedAnsi,
  isSafariFamily,
  parseTerminalFrame,
  TERMINAL_KEYS,
  terminalHello,
  wheelSequence,
} from "./terminal";

describe("TERMINAL_KEYS", () => {
  it("lists the key bar in order with the bytes each key sends", () => {
    expect(TERMINAL_KEYS.map((key) => key.label)).toEqual([
      "Esc", "Tab", "⇧Tab", "Enter", "⇧↵", "↑", "↓", "←", "→", "Paste", "^C",
    ]);
    const bytes = Object.fromEntries(TERMINAL_KEYS.map((key) => [key.label, "data" in key ? key.data : key.action]));
    expect(bytes).toMatchObject({
      Esc: "\x1b",
      Tab: "\t",
      "⇧Tab": "\x1b[Z",
      Enter: "\r",
      "⇧↵": "\n",
      "↑": "\x1b[A",
      "↓": "\x1b[B",
      "←": "\x1b[D",
      "→": "\x1b[C",
      Paste: "paste",
      "^C": "\x03",
    });
  });
});

describe("wheelSequence", () => {
  it("reports SGR wheel up and down at the grid's centre", () => {
    expect(wheelSequence("up", { cols: 120, rows: 34 })).toBe("\x1b[<64;60;17M");
    expect(wheelSequence("down", { cols: 120, rows: 34 })).toBe("\x1b[<65;60;17M");
  });

  it("falls back to 80×24 and never reports cell 0", () => {
    expect(wheelSequence("up", null)).toBe("\x1b[<64;40;12M");
    expect(wheelSequence("up", { cols: 1, rows: 1 })).toBe("\x1b[<64;1;1M");
  });
});

describe("terminalHello", () => {
  it("carries dimensions only when given", () => {
    expect(terminalHello({ cols: 100, rows: 30 }, false)).toEqual({
      type: "hello",
      terminal_protocol: 2,
      cols: 100,
      rows: 30,
    });
    expect(terminalHello(null, false)).toEqual({ type: "hello", terminal_protocol: 2 });
    expect(terminalHello({ cols: 0, rows: 0 }, false)).toEqual({ type: "hello", terminal_protocol: 2 });
  });

  it("asks for an interactive pane when unlocked", () => {
    expect(terminalHello(null, true)).toEqual({ type: "hello", terminal_protocol: 2, interactive: true });
  });
});

describe("parseTerminalFrame", () => {
  it("reads size and appearance control frames", () => {
    expect(parseTerminalFrame('{"type":"size","cols":120,"rows":34}')).toEqual({ kind: "size", cols: 120, rows: 34 });
    expect(parseTerminalFrame('{"type":"appearance","appearance":"light"}')).toEqual({
      kind: "appearance",
      appearance: "light",
    });
  });

  it("treats anything else as output", () => {
    expect(parseTerminalFrame("\x1b[?2026hhi")).toEqual({ kind: "output", text: "\x1b[?2026hhi" });
    expect(parseTerminalFrame("{not json")).toEqual({ kind: "output", text: "{not json" });
    expect(parseTerminalFrame('{"type":"appearance","appearance":"sepia"}')).toEqual({
      kind: "output",
      text: '{"type":"appearance","appearance":"sepia"}',
    });
  });
});

describe("isSafariFamily", () => {
  const chrome = "Mozilla/5.0 (Macintosh) AppleWebKit/537.36 Chrome/130.0 Safari/537.36";
  const safari = "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15";
  const iphone = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0) AppleWebKit/605.1.15 CriOS/130.0 Mobile Safari/604.1";

  it("matches Safari and every iOS browser", () => {
    expect(isSafariFamily(safari, 0)).toBe(true);
    expect(isSafariFamily(iphone, 5)).toBe(true);
    // iPadOS reports a Mac user agent with touch points.
    expect(isSafariFamily(chrome, 5)).toBe(true);
  });

  it("leaves Chromium on the desktop out", () => {
    expect(isSafariFamily(chrome, 0)).toBe(false);
  });
});

describe("buildExtendedAnsi", () => {
  it("builds the 240 fixed colours from index 16", () => {
    const palette = buildExtendedAnsi();
    expect(palette).toHaveLength(240);
    expect(palette[0]).toBe("#000000");
    expect(palette[215]).toBe("#ffffff");
    expect(palette[216]).toBe("#080808");
    expect(palette[239]).toBe("#eeeeee");
  });
});
