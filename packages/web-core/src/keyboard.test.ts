import type { KeyboardEvent as ReactKeyboardEvent } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  isComposingKeyEvent,
  isInlineMenuAcceptKey,
  isModifiedEnterShortcut,
  isSwitcherShortcut,
  ownsShortcutKeys,
  isUnmodifiedEnterKey,
  trapTabFocus,
} from "./keyboard";

type Keys = {
  key: string;
  ctrlKey?: boolean;
  metaKey?: boolean;
  shiftKey?: boolean;
  isComposing?: boolean;
  nativeEvent?: { isComposing?: boolean };
};
const key = (init: Keys) => ({ ctrlKey: false, metaKey: false, shiftKey: false, ...init });
const react = (init: Keys) => key(init) as unknown as ReactKeyboardEvent<HTMLTextAreaElement>;

describe("isComposingKeyEvent", () => {
  it("detects composition on the event or its native event", () => {
    expect(isComposingKeyEvent(key({ key: "a", isComposing: true }))).toBe(true);
    expect(isComposingKeyEvent(key({ key: "a", nativeEvent: { isComposing: true } }))).toBe(true);
    expect(isComposingKeyEvent(key({ key: "a" }))).toBe(false);
    expect(isComposingKeyEvent(key({ key: "a", isComposing: false, nativeEvent: {} }))).toBe(false);
  });
});

describe("isModifiedEnterShortcut", () => {
  it("accepts Cmd/Ctrl+Enter without Shift", () => {
    expect(isModifiedEnterShortcut(react({ key: "Enter", metaKey: true }))).toBe(true);
    expect(isModifiedEnterShortcut(react({ key: "Enter", ctrlKey: true }))).toBe(true);
  });

  it("rejects plain Enter, Shift+Enter, other keys and composition", () => {
    expect(isModifiedEnterShortcut(react({ key: "Enter" }))).toBe(false);
    expect(isModifiedEnterShortcut(react({ key: "Enter", metaKey: true, shiftKey: true }))).toBe(false);
    expect(isModifiedEnterShortcut(react({ key: "a", metaKey: true }))).toBe(false);
    expect(isModifiedEnterShortcut(react({ key: "Enter", metaKey: true, isComposing: true }))).toBe(false);
  });
});

describe("isUnmodifiedEnterKey", () => {
  it("accepts a bare Enter only", () => {
    expect(isUnmodifiedEnterKey(key({ key: "Enter" }))).toBe(true);
    for (const modifier of ["ctrlKey", "metaKey", "shiftKey"] as const) {
      expect(isUnmodifiedEnterKey(key({ key: "Enter", [modifier]: true }))).toBe(false);
    }
    expect(isUnmodifiedEnterKey(key({ key: "Enter", isComposing: true }))).toBe(false);
    expect(isUnmodifiedEnterKey(key({ key: "Tab" }))).toBe(false);
  });
});

describe("isInlineMenuAcceptKey", () => {
  it("accepts Tab and a bare Enter", () => {
    expect(isInlineMenuAcceptKey(key({ key: "Tab" }))).toBe(true);
    expect(isInlineMenuAcceptKey(key({ key: "Enter" }))).toBe(true);
  });

  it("accepts Tab even with modifiers or during composition, but not a modified Enter", () => {
    expect(isInlineMenuAcceptKey(key({ key: "Tab", shiftKey: true }))).toBe(true);
    expect(isInlineMenuAcceptKey(key({ key: "Tab", isComposing: true }))).toBe(true);
    expect(isInlineMenuAcceptKey(key({ key: "Enter", shiftKey: true }))).toBe(false);
    expect(isInlineMenuAcceptKey(key({ key: "ArrowDown" }))).toBe(false);
  });
});

describe("trapTabFocus", () => {
  afterEach(() => vi.unstubAllGlobals());

  // A root holding, in document order, a tab stop, a non-stop, the other tab stop
  // and a trailing non-stop; `focused` picks what holds focus.
  type Spot = "root" | "first" | "middle" | "last" | "trailing" | "outside";
  function press(focused: Spot, shiftKey: boolean, fromNonStops?: boolean) {
    const order: Spot[] = ["root", "first", "middle", "last", "trailing"];
    const moved: string[] = [];
    const nodes = new Map(
      order.map((spot) => [
        spot,
        {
          spot,
          focus: () => moved.push(spot),
          compareDocumentPosition: (other: { spot: Spot }) =>
            order.indexOf(other.spot) < order.indexOf(spot) ? 2 : 4,
        },
      ]),
    );
    const root = {
      ...nodes.get("root")!,
      querySelectorAll: () => [nodes.get("first")!, nodes.get("last")!],
      contains: (node: { spot: Spot }) => node.spot !== "outside",
    };
    const active = focused === "root" ? root : (nodes.get(focused) ?? { spot: "outside" });
    vi.stubGlobal("document", { activeElement: active });
    vi.stubGlobal("Node", { DOCUMENT_POSITION_PRECEDING: 2, DOCUMENT_POSITION_FOLLOWING: 4 });
    const event = { ...key({ key: "Tab", shiftKey }), preventDefault: vi.fn() };
    trapTabFocus(event as unknown as KeyboardEvent, root as unknown as HTMLElement, { fromNonStops });
    return moved;
  }

  it("does nothing for non-Tab keys or a missing root", () => {
    expect(trapTabFocus(key({ key: "a" }) as unknown as KeyboardEvent, {} as HTMLElement)).toBe(false);
    expect(trapTabFocus(key({ key: "Tab" }) as unknown as KeyboardEvent, null)).toBe(false);
  });

  it("wraps at either end and pulls focus back from outside", () => {
    expect(press("last", false)).toEqual(["first"]);
    expect(press("first", true)).toEqual(["last"]);
    expect(press("outside", false)).toEqual(["first"]);
    expect(press("first", false)).toEqual([]);
  });

  it("wraps from a non-stop with nothing beyond it, only when asked to", () => {
    expect(press("root", true, true)).toEqual(["last"]);
    expect(press("trailing", false, true)).toEqual(["first"]);
    expect(press("root", true)).toEqual([]);
    expect(press("trailing", false)).toEqual([]);
  });

  it("leaves Tab to the browser from a non-stop with a stop beyond it", () => {
    expect(press("root", false, true)).toEqual([]);
    expect(press("middle", false, true)).toEqual([]);
    expect(press("middle", true, true)).toEqual([]);
    expect(press("trailing", true, true)).toEqual([]);
  });
});

describe("ownsShortcutKeys", () => {
  const target = (matches: boolean) => ({ closest: vi.fn(() => (matches ? {} : null)) }) as unknown as EventTarget;

  it("is true inside a terminal or an editable region", () => {
    expect(ownsShortcutKeys(target(true))).toBe(true);
    expect(ownsShortcutKeys(target(false))).toBe(false);
  });

  it("asks for a terminal and for editable text that is not turned off", () => {
    const closest = vi.fn(() => null);
    ownsShortcutKeys({ closest } as unknown as EventTarget);
    expect(closest).toHaveBeenCalledWith('.xterm, [contenteditable]:not([contenteditable="false"])');
  });

  it("is false for no target or a target that is not an element", () => {
    expect(ownsShortcutKeys(null)).toBe(false);
    expect(ownsShortcutKeys({} as EventTarget)).toBe(false);
  });
});

describe("isSwitcherShortcut", () => {
  const key = (overrides: Record<string, unknown> = {}) =>
    ({ key: "k", metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, ...overrides }) as Parameters<
      typeof isSwitcherShortcut
    >[0];

  it("is Cmd or Ctrl plus K, and nothing else", () => {
    expect(isSwitcherShortcut(key({ metaKey: true }))).toBe(true);
    expect(isSwitcherShortcut(key({ ctrlKey: true, key: "K" }))).toBe(true);
    expect(isSwitcherShortcut(key())).toBe(false);
    expect(isSwitcherShortcut(key({ ctrlKey: true, shiftKey: true }))).toBe(false);
    expect(isSwitcherShortcut(key({ ctrlKey: true, altKey: true }))).toBe(false);
    expect(isSwitcherShortcut(key({ ctrlKey: true, key: "j" }))).toBe(false);
    expect(isSwitcherShortcut(key({ ctrlKey: true, isComposing: true }))).toBe(false);
  });
});
