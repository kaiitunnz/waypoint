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

  it("reads an inset of 0 once iOS has panned the visual viewport to the frame's bottom, still open", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516, visualOffsetTop: 336 }, true)).toEqual({ inset: 0, visibleHeight: 516 });
  });

  it("reads an iOS home-screen app that scrolls the page above the keyboard as open", () => {
    expect(keyboardState({ ...PHONE, layoutHeight: 793, visualHeight: 483, visualOffsetTop: 310 }, true)).toEqual({ inset: 0, visibleHeight: 483 });
  });

  it("is closed when the frame shrank with the visual viewport", () => {
    expect(keyboardState({ ...PHONE, layoutHeight: 516, visualHeight: 516 }, true)).toEqual(KEYBOARD_CLOSED);
  });

  it("reads a landscape screen from its own heights", () => {
    expect(keyboardState({ ...PHONE, layoutHeight: 393, visualHeight: 193 }, true)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  it("never reads a negative inset when the page is panned past the keyboard", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 516, visualOffsetTop: 400 }, true)).toEqual({ inset: 0, visibleHeight: 516 });
  });

  it("is closed while pinch-zoomed", () => {
    expect(keyboardState({ ...PHONE, visualHeight: 426, visualOffsetTop: 100, scale: 2 }, true)).toEqual(KEYBOARD_CLOSED);
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

function fakes() {
  const frames: (() => void)[] = [];
  const requested = { count: 0 };
  const viewport = Object.assign(new EventTarget(), { height: 852, offsetTop: 0, scale: 1 });
  const frame = { height: 852 };
  const winTarget = new EventTarget();
  const win = {
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
  const flush = () => frames.splice(0).forEach((frameCallback) => frameCallback());
  const seen: unknown[] = [];
  const watch = (onChange: (state: Parameters<Parameters<typeof watchKeyboard>[3]>[0]) => void = (state) => void seen.push(state)) =>
    watchKeyboard(win as unknown as KeyboardWindow, doc as unknown as KeyboardDocument, () => frame.height, onChange);
  return { win, doc, viewport, frame, winTarget, docTarget, flush, requested, seen, watch };
}

const INPUT: FocusTarget = { tagName: "INPUT", type: "text" };

describe("watchKeyboard", () => {
  it("reports the state now, then each change a frame after its event", () => {
    const { doc, viewport, docTarget, flush, seen, watch } = fakes();
    watch();
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
    const { viewport, docTarget, winTarget, requested, watch } = fakes();
    watch(vi.fn());
    docTarget.dispatchEvent(new Event("focusin"));
    viewport.dispatchEvent(new Event("resize"));
    viewport.dispatchEvent(new Event("scroll"));
    winTarget.dispatchEvent(new Event("resize"));
    expect(requested.count).toBe(1);
  });

  // A keyboard that rises after the watcher has read the viewport at rest.
  function opened() {
    const parts = fakes();
    parts.doc.activeElement = INPUT;
    parts.watch();
    parts.viewport.height = 516;
    parts.viewport.dispatchEvent(new Event("resize"));
    parts.flush();
    return parts;
  }

  it("stays open as focus moves from one field to the next", () => {
    const { doc, docTarget, flush, seen } = opened();
    doc.activeElement = null;
    docTarget.dispatchEvent(new Event("focusout"));
    doc.activeElement = { tagName: "TEXTAREA" };
    docTarget.dispatchEvent(new Event("focusin"));
    flush();
    expect(seen).toEqual([KEYBOARD_CLOSED, { inset: 336, visibleHeight: 516 }]);
  });

  it("closes when the focus leaves for good", () => {
    const { doc, docTarget, flush, seen } = opened();
    doc.activeElement = null;
    docTarget.dispatchEvent(new Event("focusout"));
    flush();
    expect(seen).toEqual([KEYBOARD_CLOSED, { inset: 336, visibleHeight: 516 }, KEYBOARD_CLOSED]);
  });

  it("reads a keyboard that is up when the watcher starts", () => {
    const { doc, viewport, seen, watch } = fakes();
    doc.activeElement = INPUT;
    viewport.height = 516;
    watch();
    expect(seen).toEqual([{ inset: 336, visibleHeight: 516 }]);
  });

  it("reads a rotated screen from its new heights", () => {
    const { doc, viewport, frame, winTarget, flush, seen, watch } = fakes();
    doc.activeElement = INPUT;
    watch();
    frame.height = 393;
    viewport.height = 193;
    winTarget.dispatchEvent(new Event("resize"));
    flush();
    expect(seen.at(-1)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  describe("in an iOS home-screen app", () => {
    // The keyboard shrinks the layout viewport and scrolls the page, the fixed frame keeps its
    // height, and dismissing the keyboard leaves the frame short of the screen.
    it("reads open above the keyboard, closed after it is dismissed with the field still focused", () => {
      const { doc, viewport, frame, flush, seen, watch } = fakes();
      doc.activeElement = INPUT;
      frame.height = 852;
      viewport.height = 852;
      watch();
      expect(seen).toEqual([KEYBOARD_CLOSED]);

      frame.height = 793;
      viewport.height = 483;
      viewport.offsetTop = 310;
      viewport.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual({ inset: 0, visibleHeight: 483 });

      viewport.height = 793;
      viewport.offsetTop = 0;
      viewport.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
    });
  });

  it("stops reporting once unsubscribed", () => {
    const { doc, viewport, flush, watch } = fakes();
    doc.activeElement = INPUT;
    const onChange = vi.fn();
    const stop = watch(onChange);
    stop();
    viewport.height = 516;
    viewport.dispatchEvent(new Event("resize"));
    flush();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("drops a read that was pending when it was unsubscribed", () => {
    const { doc, viewport, flush, watch } = fakes();
    doc.activeElement = INPUT;
    const onChange = vi.fn();
    const stop = watch(onChange);
    viewport.height = 516;
    viewport.dispatchEvent(new Event("resize"));
    stop();
    flush();
    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it("reports closed once for a browser without a visual viewport", () => {
    const { win, watch } = fakes();
    win.visualViewport = null as never;
    const onChange = vi.fn();
    watch(onChange)();
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
