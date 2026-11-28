import { describe, expect, it, vi } from "vitest";

import {
  isTextEntry,
  KEYBOARD_CLOSED,
  keyboardState,
  publishKeyboard,
  trackRestGap,
  UNKNOWN_REST_GAP,
  watchKeyboard,
  type FocusTarget,
  type KeyboardDocument,
  type KeyboardRoot,
  type KeyboardWindow,
} from "./keyboardInset";

const PHONE = { layoutHeight: 852, visualHeight: 852, visualOffsetTop: 0, scale: 1, restGap: 0 };

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
    expect(keyboardState({ ...PHONE, layoutHeight: 393, visualHeight: 193 }, true)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  describe("with a gap between the viewports at rest", () => {
    const BIASED = { ...PHONE, visualHeight: 759, restGap: 93 };

    it("is closed with no keyboard up, whatever has focus", () => {
      expect(keyboardState(BIASED, true)).toEqual(KEYBOARD_CLOSED);
    });

    it("covers only what the keyboard takes on top of the gap", () => {
      expect(keyboardState({ ...BIASED, visualHeight: 459 }, true)).toEqual({ inset: 300, visibleHeight: 459 });
    });

    it("subtracts the pan as well", () => {
      expect(keyboardState({ ...BIASED, visualHeight: 459, visualOffsetTop: 120 }, true)).toEqual({ inset: 180, visibleHeight: 459 });
    });
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

describe("trackRestGap", () => {
  const view = { layoutWidth: 393, layoutHeight: 852, visualHeight: 759, scale: 1 };
  const learned = trackRestGap(UNKNOWN_REST_GAP, view, true);

  it("learns the first reading, typing or not", () => {
    expect(learned).toEqual({ width: 393, gap: 93 });
    expect(trackRestGap(UNKNOWN_REST_GAP, view, false)).toEqual({ width: 393, gap: 93 });
  });

  it("keeps the smallest gap while a field has focus", () => {
    expect(trackRestGap(learned, { ...view, visualHeight: 800 }, true).gap).toBe(52);
    expect(trackRestGap(learned, { ...view, visualHeight: 740 }, true).gap).toBe(93);
  });

  it("takes the latest gap with no field focused, so a low reading heals", () => {
    const low = trackRestGap(learned, { ...view, visualHeight: 852 }, true);
    expect(low.gap).toBe(0);
    expect(trackRestGap(low, view, false).gap).toBe(93);
  });

  it("does not take a keyboard for a rest gap, nor a viewport taller than the layout", () => {
    expect(trackRestGap(learned, { ...view, visualHeight: 459 }, false)).toEqual(learned);
    expect(trackRestGap(UNKNOWN_REST_GAP, { ...view, visualHeight: 459 }, true).gap).toBeNull();
    expect(trackRestGap(learned, { ...view, visualHeight: 860 }, false)).toEqual(learned);
  });

  it("starts over when the window changes width", () => {
    expect(trackRestGap(learned, { ...view, layoutWidth: 852, layoutHeight: 393, visualHeight: 273 }, true)).toEqual({ width: 852, gap: 120 });
    expect(trackRestGap(learned, { ...view, layoutWidth: 852, layoutHeight: 393, visualHeight: 100 }, true)).toEqual({ width: 852, gap: null });
  });

  it("does not learn from a zoomed viewport", () => {
    expect(trackRestGap(learned, { ...view, visualHeight: 426, scale: 2 }, true)).toBe(learned);
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
    innerWidth: 393,
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

  // A keyboard that rises after the watcher has read the viewport at rest.
  function opened() {
    const parts = fakes();
    parts.doc.activeElement = INPUT;
    const seen: unknown[] = [];
    watchKeyboard(parts.win, parts.doc, (state) => seen.push(state));
    parts.viewport.height = 516;
    parts.viewport.dispatchEvent(new Event("resize"));
    parts.flush();
    return { ...parts, seen };
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

  it("reads a rotated screen from its new heights", () => {
    const { win, doc, viewport, winTarget, flush } = fakes();
    doc.activeElement = INPUT;
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    win.innerWidth = 852;
    win.innerHeight = 393;
    viewport.height = 393;
    winTarget.dispatchEvent(new Event("resize"));
    flush();
    viewport.height = 193;
    viewport.dispatchEvent(new Event("resize"));
    flush();
    expect(seen.at(-1)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  it("reads a keyboard that is up when the watcher starts", () => {
    const { win, doc, viewport } = fakes();
    doc.activeElement = INPUT;
    viewport.height = 516;
    const seen: unknown[] = [];
    watchKeyboard(win, doc, (state) => seen.push(state));
    expect(seen).toEqual([{ inset: 336, visibleHeight: 516 }]);
  });

  it("reads a keyboard that stays up through a rotation", () => {
    const { win, doc, viewport, winTarget, flush, seen } = opened();
    void doc;
    win.innerWidth = 852;
    win.innerHeight = 393;
    viewport.height = 193;
    winTarget.dispatchEvent(new Event("resize"));
    flush();
    expect(seen.at(-1)).toEqual({ inset: 200, visibleHeight: 193 });
  });

  describe("with a gap between the viewports at rest", () => {
    function biased() {
      const parts = fakes();
      parts.viewport.height = 759;
      parts.doc.activeElement = INPUT;
      const seen: unknown[] = [];
      watchKeyboard(parts.win, parts.doc, (state) => seen.push(state));
      return { ...parts, seen };
    }

    it("reads closed from the start, with a field already focused", () => {
      expect(biased().seen).toEqual([KEYBOARD_CLOSED]);
    });

    it("covers only the keyboard, and is closed again once it is dismissed while the field keeps focus", () => {
      const { viewport, flush, seen } = biased();
      viewport.height = 459;
      viewport.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual({ inset: 300, visibleHeight: 459 });

      viewport.height = 759;
      viewport.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
    });

    it("heals a reading that was too low once no field has focus", () => {
      const parts = fakes();
      parts.doc.activeElement = INPUT;
      const seen: unknown[] = [];
      watchKeyboard(parts.win, parts.doc, (state) => seen.push(state));
      parts.viewport.height = 759;
      parts.viewport.dispatchEvent(new Event("resize"));
      parts.flush();
      expect(seen.at(-1)).toEqual({ inset: 93, visibleHeight: 759 });

      parts.doc.activeElement = null;
      parts.docTarget.dispatchEvent(new Event("focusout"));
      parts.flush();
      parts.doc.activeElement = INPUT;
      parts.docTarget.dispatchEvent(new Event("focusin"));
      parts.flush();
      expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
    });

    it("learns the gap again after a rotation", () => {
      const { win, viewport, winTarget, flush, seen } = biased();
      win.innerWidth = 852;
      win.innerHeight = 393;
      viewport.height = 273;
      winTarget.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual(KEYBOARD_CLOSED);
      viewport.height = 73;
      viewport.dispatchEvent(new Event("resize"));
      flush();
      expect(seen.at(-1)).toEqual({ inset: 200, visibleHeight: 73 });
    });
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
