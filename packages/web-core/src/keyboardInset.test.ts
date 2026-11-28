import { describe, expect, it, vi } from "vitest";

import {
  currentKeyboardState,
  isTextEntry,
  KEYBOARD_CLOSED,
  keyboardState,
  scrollDelta,
  watchKeyboard,
  type FocusTarget,
  type KeyboardDocument,
  type KeyboardWindow,
} from "./keyboardInset";

const PHONE = { layoutHeight: 852, visualHeight: 852, visualOffsetTop: 0, scale: 1 };

describe("keyboardState", () => {
  it("is closed with the keyboard down", () => {
    expect(keyboardState(PHONE, true)).toEqual(KEYBOARD_CLOSED);
  });

  it("covers the height the visual viewport lost when the keyboard is up", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516 }, true)).toEqual({ inset: 336, visibleHeight: 516 });
  });

  it("subtracts the pan of the visual viewport", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516, visualOffsetTop: 120 }, true)).toEqual({ inset: 216, visibleHeight: 516 });
  });

  it("reads an inset of 0 once iOS has panned the visual viewport to the layout bottom, still open", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516, visualOffsetTop: 336 }, true)).toEqual({ inset: 0, visibleHeight: 516 });
  });

  it("is closed when the browser resized the layout viewport itself", () => {
    expect(keyboardState({ ...PHONE, layoutHeight: 516, visualHeight: 516 }, true)).toEqual(KEYBOARD_CLOSED);
  });

  it("reads each orientation from its own heights", () => {
    expect(keyboardState({ layoutHeight: 393, visualHeight: 193, visualOffsetTop: 0, scale: 1 }, true).inset).toBe(200);
    expect(keyboardState(PHONE, true)).toEqual(KEYBOARD_CLOSED);
  });

  it("is closed while pinch-zoomed", () => {
    expect(keyboardState({ layoutHeight: 852, visualHeight: 426, visualOffsetTop: 100, scale: 2 }, true)).toEqual(KEYBOARD_CLOSED);
  });

  it("is closed unless a field that takes typing has focus", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516 }, false)).toEqual(KEYBOARD_CLOSED);
  });

  it("ignores a gap under the tolerance and rounds the rest", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 848.4 }, true)).toEqual(KEYBOARD_CLOSED);
    expect(keyboardState({ ...PHONE, visualHeight: 516.4 }, true)).toEqual({ inset: 336, visibleHeight: 516 });
  });

  it("is closed for a viewport it cannot read", () => {
    expect(keyboardState({ ...PHONE, visualHeight: Number.NaN }, true)).toEqual(KEYBOARD_CLOSED);
    expect(keyboardState({ ...PHONE, scale: Number.POSITIVE_INFINITY }, true)).toEqual(KEYBOARD_CLOSED);
  });
});

describe("isTextEntry", () => {
  const field = (tagName: string, extra: Partial<FocusTarget> = {}): FocusTarget => ({ tagName, ...extra });

  it("accepts text inputs, textareas, selects and contenteditable", () => {
    expect(isTextEntry(field("INPUT", { type: "text" }))).toBe(true);
    expect(isTextEntry(field("INPUT", { type: "search" }))).toBe(true);
    expect(isTextEntry(field("INPUT"))).toBe(true);
    expect(isTextEntry(field("TEXTAREA"))).toBe(true);
    expect(isTextEntry(field("SELECT"))).toBe(true);
    expect(isTextEntry(field("DIV", { isContentEditable: true }))).toBe(true);
  });

  it("rejects what no keyboard rises for", () => {
    expect(isTextEntry(field("INPUT", { type: "checkbox" }))).toBe(false);
    expect(isTextEntry(field("INPUT", { type: "file" }))).toBe(false);
    expect(isTextEntry(field("BUTTON"))).toBe(false);
    expect(isTextEntry(field("DIV"))).toBe(false);
    expect(isTextEntry(null)).toBe(false);
  });
});

type Frames = (() => void)[];

function fakes() {
  const frames: Frames = [];
  const viewport = Object.assign(new EventTarget(), { height: 852, offsetTop: 0, scale: 1 });
  const winTarget = new EventTarget();
  const win = {
    innerHeight: 852,
    visualViewport: viewport,
    addEventListener: (type: string, listener: () => void) => winTarget.addEventListener(type, listener),
    removeEventListener: (type: string, listener: () => void) => winTarget.removeEventListener(type, listener),
    requestAnimationFrame: (callback: () => void) => frames.push(callback),
    cancelAnimationFrame: (handle: number) => {
      frames[handle - 1] = () => {};
    },
  };
  const docTarget = new EventTarget();
  const doc = {
    activeElement: null as FocusTarget | null,
    addEventListener: (type: string, listener: () => void) => docTarget.addEventListener(type, listener),
    removeEventListener: (type: string, listener: () => void) => docTarget.removeEventListener(type, listener),
  };
  const flush = () => frames.splice(0).forEach((frame) => frame());
  return { win: win as unknown as KeyboardWindow, doc: doc as unknown as KeyboardDocument & { activeElement: FocusTarget | null }, viewport, winTarget, docTarget, flush };
}

const INPUT: FocusTarget = { tagName: "INPUT", type: "text" };

describe("watchKeyboard", () => {
  it("reports the state now, then each change a frame after its event", () => {
    const { win, doc, viewport, docTarget, flush } = fakes();
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    expect(seen).toEqual([KEYBOARD_CLOSED]);

    doc.activeElement = INPUT;
    docTarget.dispatchEvent(new Event("focusin"));
    viewport.height = 516;
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
    expect(seen).toHaveLength(1);
    flush();
    expect(seen).toEqual([KEYBOARD_CLOSED, { inset: 336, visibleHeight: 516 }]);

    viewport.offsetTop = 100;
    viewport.dispatchEvent(new Event("scroll"));
    flush();
    expect(seen.at(-1)).toEqual({ inset: 236, visibleHeight: 516 });

    viewport.height = 852;
    viewport.offsetTop = 0;
    viewport.dispatchEvent(new Event("resize"));
    flush();
    expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
  });

  it("stays open as focus moves from one field to the next", () => {
    const { win, doc, viewport, docTarget, flush } = fakes();
    doc.activeElement = INPUT;
    viewport.height = 516;
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    doc.activeElement = null;
    docTarget.dispatchEvent(new Event("focusout"));
    doc.activeElement = { tagName: "TEXTAREA" };
    docTarget.dispatchEvent(new Event("focusin"));
    flush();
    expect(seen).toEqual([{ inset: 336, visibleHeight: 516 }]);
  });

  it("closes when the focus leaves for good", () => {
    const { win, doc, viewport, docTarget, flush } = fakes();
    doc.activeElement = INPUT;
    viewport.height = 516;
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    doc.activeElement = null;
    docTarget.dispatchEvent(new Event("focusout"));
    flush();
    expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
  });

  it("re-reads after a rotation reported on the window", () => {
    const { win, doc, viewport, winTarget, flush } = fakes();
    doc.activeElement = INPUT;
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    win.innerHeight = 393;
    viewport.height = 193;
    winTarget.dispatchEvent(new Event("resize"));
    flush();
    expect(seen.at(-1)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  it("stops reporting once unsubscribed", () => {
    const { win, doc, viewport, flush } = fakes();
    doc.activeElement = INPUT;
    const onChange = vi.fn();
    const stop = watchKeyboard(win, doc, onChange);
    stop();
    viewport.height = 516;
    viewport.dispatchEvent(new Event("resize"));
    flush();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("reports closed once for a browser without a visual viewport", () => {
    const { win, doc } = fakes();
    win.visualViewport = null;
    const onChange = vi.fn();
    watchKeyboard(win, doc, onChange)();
    expect(currentKeyboardState(win, doc)).toEqual(KEYBOARD_CLOSED);
    expect(onChange).toHaveBeenCalledExactlyOnceWith(KEYBOARD_CLOSED);
  });
});

describe("scrollDelta", () => {
  const view = { top: 0, bottom: 500 };

  it("is 0 for a field already in view", () => {
    expect(scrollDelta({ top: 100, bottom: 144 }, view)).toBe(0);
  });

  it("scrolls down by what lies below the view, plus a margin", () => {
    expect(scrollDelta({ top: 520, bottom: 564 }, view)).toBe(72);
  });

  it("scrolls up by what lies above the view, plus a margin", () => {
    expect(scrollDelta({ top: -40, bottom: 4 }, view)).toBe(-48);
  });

  it("lines up the top of a field taller than the view", () => {
    expect(scrollDelta({ top: 200, bottom: 900 }, view)).toBe(200);
  });
});
