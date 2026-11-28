import { describe, expect, it, vi } from "vitest";

import {
  isTextEntry,
  KEYBOARD_CLOSED,
  keyboardState,
  publishKeyboard,
  watchKeyboard,
  type FocusTarget,
  type KeyboardDocument,
  type KeyboardRoot,
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

  it("reads a landscape screen from its own heights", () => {
    expect(keyboardState({ layoutHeight: 393, visualHeight: 193, visualOffsetTop: 0, scale: 1 }, true)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  it("never reads a negative inset when the page is panned past the keyboard", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516, visualOffsetTop: 400 }, true)).toEqual({ inset: 0, visibleHeight: 516 });
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
  const requested = { count: 0 };
  const viewport = Object.assign(new EventTarget(), { height: 852, offsetTop: 0, scale: 1 });
  const winTarget = new EventTarget();
  const win = {
    innerHeight: 852,
    visualViewport: viewport,
    addEventListener: (type: string, listener: () => void) => winTarget.addEventListener(type, listener),
    removeEventListener: (type: string, listener: () => void) => winTarget.removeEventListener(type, listener),
    requestAnimationFrame: (callback: () => void) => {
      requested.count += 1;
      return frames.push(callback);
    },
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
  return { win: win as unknown as KeyboardWindow, doc: doc as unknown as KeyboardDocument & { activeElement: FocusTarget | null }, viewport, winTarget, docTarget, flush, requested };
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

  it("reads once a frame however many events arrive", () => {
    const { win, doc, viewport, docTarget, winTarget, requested } = fakes();
    watchKeyboard(win, doc, vi.fn());
    docTarget.dispatchEvent(new Event("focusin"));
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
    winTarget.dispatchEvent(new Event("resize"));
    expect(requested.count).toBe(1);
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

  it("re-reads when the window resizes", () => {
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

  it("drops a read that was pending when it was unsubscribed", () => {
    const { win, doc, viewport, flush } = fakes();
    doc.activeElement = INPUT;
    const onChange = vi.fn();
    const stop = watchKeyboard(win, doc, onChange);
    viewport.height = 516;
    viewport.dispatchEvent(new Event("resize"));
    stop();
    flush();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("reports closed once for a browser without a visual viewport", () => {
    const { win, doc } = fakes();
    win.visualViewport = null;
    const onChange = vi.fn();
    watchKeyboard(win, doc, onChange)();
    expect(onChange).toHaveBeenCalledExactlyOnceWith(KEYBOARD_CLOSED);
  });
});

describe("publishKeyboard", () => {
  function root() {
    const values = new Map<string, string>();
    const dataset: Record<string, string | undefined> = {};
    const fake: KeyboardRoot = {
      style: {
        setProperty: (name, value) => void values.set(name, value),
        removeProperty: (name) => values.delete(name),
      },
      dataset,
    };
    return { fake, values, dataset };
  }

  it("sets the inset, the height in view and the flag while the keyboard is open", () => {
    const { fake, values, dataset } = root();
    publishKeyboard(fake, { inset: 336, visibleHeight: 516 });
    expect(Object.fromEntries(values)).toEqual({ "--keyboard-inset": "336px", "--visible-height": "516px" });
    expect(dataset.keyboard).toBe("");
  });

  it("keeps the flag while iOS has panned the page and the inset reads 0", () => {
    const { fake, values, dataset } = root();
    publishKeyboard(fake, { inset: 0, visibleHeight: 516 });
    expect(values.get("--keyboard-inset")).toBe("0px");
    expect(dataset.keyboard).toBe("");
  });

  it("removes all three once the keyboard is closed", () => {
    const { fake, values, dataset } = root();
    publishKeyboard(fake, { inset: 336, visibleHeight: 516 });
    publishKeyboard(fake, KEYBOARD_CLOSED);
    expect(values.size).toBe(0);
    expect("keyboard" in dataset).toBe(false);
  });
});
