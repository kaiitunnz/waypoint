import { describe, expect, it, vi } from "vitest";

import { KEYBOARD_CLOSED, type FocusTarget, type KeyboardDocument } from "./keyboardInset";
import { watchTouchViewport, type BoxHost, type TouchViewportWindow } from "./touchViewport";

const INPUT: FocusTarget = { tagName: "INPUT", type: "text" };
const TEXTAREA: FocusTarget = { tagName: "TEXTAREA" };

function target() {
  const events = new EventTarget();
  return {
    addEventListener: (type: string, listener: () => void) => events.addEventListener(type, listener),
    removeEventListener: (type: string, listener: () => void) => events.removeEventListener(type, listener),
    fire: (type: string) => events.dispatchEvent(new Event(type)),
  };
}

// An iPhone 852px tall: `screen` is what `100lvh` measures, `frame` the fixed `inset: 0` box.
function setup({ homeScreenApp = false, visualViewport = true } = {}) {
  const heights = { frame: 852, screen: 852 };
  const boxes: { css: string; removed: boolean }[] = [];
  const host: BoxHost = {
    createBox(css) {
      const box = { css, removed: false };
      boxes.push(box);
      return {
        height: () => (css.includes("inset:0") ? heights.frame : css.includes("100lvh") ? heights.screen : 3000),
        remove: () => void (box.removed = true),
      };
    },
  };
  const viewportEvents = target();
  const viewport = Object.assign(viewportEvents, { height: 852, offsetTop: 0, scale: 1 });
  const winEvents = target();
  const docEvents = target();
  const doc = Object.assign(docEvents, { activeElement: null as FocusTarget | null });

  let now = 0;
  let next = 1;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const frames = new Map<number, () => void>();
  const win = Object.assign(winEvents, {
    visualViewport: visualViewport ? viewport : null,
    requestAnimationFrame: (callback: () => void) => {
      const handle = next++;
      frames.set(handle, callback);
      return handle;
    },
    cancelAnimationFrame: (handle: number) => void frames.delete(handle),
    setTimeout: (callback: () => void, ms: number) => {
      const handle = next++;
      timers.set(handle, { at: now + ms, callback });
      return handle;
    },
    clearTimeout: (handle: number) => void timers.delete(handle),
  });

  const published: unknown[] = [];
  const reveal = vi.fn();
  const stop = watchTouchViewport({
    win: win as unknown as TouchViewportWindow,
    doc: doc as unknown as KeyboardDocument,
    host,
    homeScreenApp,
    publish: (state) => void published.push(state),
    reveal,
  });

  // Runs the animation frames queued so far.
  const flush = () => [...frames].forEach(([handle, callback]) => (frames.delete(handle), callback()));
  const advance = (ms: number) => {
    const end = now + ms;
    for (;;) {
      const due = [...timers.entries()].filter(([, t]) => t.at <= end).sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      timers.delete(due[0]);
      now = due[1].at;
      due[1].callback();
    }
    now = end;
  };
  const lifts = () => boxes.filter((box) => box.css.includes("3000px"));
  const keyboardUp = (visible = 483, offsetTop = 310) => {
    viewport.height = visible;
    viewport.offsetTop = offsetTop;
    viewport.fire("resize");
    flush();
  };
  return { heights, boxes, viewport, win, doc, published, reveal, stop, flush, advance, lifts, keyboardUp, timers, frames };
}

describe("watchTouchViewport", () => {
  describe("the keyboard", () => {
    it("is measured against the fixed inset-0 box, not the window", () => {
      const { heights, boxes, viewport, doc, published, flush } = setup();
      doc.activeElement = INPUT;
      heights.frame = 793;
      viewport.height = 483;
      viewport.offsetTop = 310;
      viewport.fire("resize");
      flush();
      expect(boxes[0].css).toContain("position:fixed;inset:0");
      expect(published).toEqual([{ inset: 0, visibleHeight: 483 }]);
    });

    it("publishes each change once, and nothing for a read that changes nothing", () => {
      const { doc, viewport, published, flush, keyboardUp } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      viewport.fire("scroll");
      flush();
      expect(published).toEqual([{ inset: 336, visibleHeight: 516 }]);
      keyboardUp(852, 0);
      expect(published).toEqual([{ inset: 336, visibleHeight: 516 }, KEYBOARD_CLOSED]);
    });

    it("follows a pan of the visual viewport in the inset", () => {
      const { doc, published, keyboardUp } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      keyboardUp(516, 100);
      expect(published.at(-1)).toEqual({ inset: 236, visibleHeight: 516 });
    });
  });

  describe("revealing the focused field", () => {
    it("waits a frame after the keyboard rises, for the layout to take the published values", () => {
      const { doc, reveal, keyboardUp, flush } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      expect(reveal).not.toHaveBeenCalled();
      flush();
      expect(reveal).toHaveBeenCalledTimes(1);
    });

    it("does not reveal again when the visual viewport only pans", () => {
      const { doc, reveal, keyboardUp, flush } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      flush();
      keyboardUp(516, 60);
      keyboardUp(516, 120);
      flush();
      expect(reveal).toHaveBeenCalledTimes(1);
    });

    it("reveals again when the visible height changes", () => {
      const { doc, reveal, keyboardUp, flush } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      flush();
      keyboardUp(480, 0);
      flush();
      expect(reveal).toHaveBeenCalledTimes(2);
    });

    it("reveals at once when focus moves to another field while the keyboard is up", () => {
      const { doc, reveal, keyboardUp, flush } = setup();
      doc.activeElement = INPUT;
      keyboardUp(516, 0);
      flush();
      reveal.mockClear();
      doc.activeElement = TEXTAREA;
      doc.fire("focusin");
      flush();
      expect(reveal).toHaveBeenCalledTimes(1);
    });

    it("does not reveal while no keyboard is up", () => {
      const { doc, reveal, flush } = setup();
      doc.activeElement = INPUT;
      doc.fire("focusin");
      flush();
      doc.activeElement = TEXTAREA;
      doc.fire("focusin");
      flush();
      expect(reveal).not.toHaveBeenCalled();
    });
  });

  describe("restoring the frame", () => {
    it("is inert outside a home-screen app: no screen box, no lift", () => {
      const { boxes, heights, win, flush, advance, lifts } = setup({ homeScreenApp: false });
      heights.frame = 793;
      win.fire("resize");
      flush();
      advance(5000);
      expect(boxes).toHaveLength(1);
      expect(lifts()).toHaveLength(0);
    });

    it("lifts at once, and lets go when the frame is back", () => {
      const { boxes, heights, win, flush, advance, lifts } = setup({ homeScreenApp: true });
      expect(boxes.some((box) => box.css.includes("100lvh"))).toBe(true);
      heights.frame = 793;
      win.fire("resize");
      flush();
      advance(0);
      expect(lifts()).toHaveLength(1);
      heights.frame = 852;
      advance(60);
      expect(lifts()[0].removed).toBe(true);
    });

    it("lifts a frame found short at launch", () => {
      const parts = setup({ homeScreenApp: true });
      parts.heights.frame = 793;
      parts.viewport.height = 793;
      parts.win.fire("resize");
      parts.flush();
      parts.advance(0);
      expect(parts.lifts()).toHaveLength(1);
    });

    it("never lifts while the keyboard is up", () => {
      const { doc, heights, advance, lifts, keyboardUp } = setup({ homeScreenApp: true });
      doc.activeElement = INPUT;
      heights.frame = 793;
      keyboardUp(483, 310);
      advance(5000);
      expect(lifts()).toHaveLength(0);
    });

    it("never lifts while the keyboard is up under a pinch zoom, which reads as no keyboard", () => {
      const { doc, heights, viewport, published, advance, lifts, keyboardUp } = setup({ homeScreenApp: true });
      doc.activeElement = INPUT;
      heights.frame = 793;
      viewport.scale = 2;
      keyboardUp(400, 0);
      advance(5000);
      expect(published).toEqual([]);
      expect(lifts()).toHaveLength(0);
    });

    it("lifts once the keyboard is dismissed with the field still focused", () => {
      const { doc, heights, advance, lifts, keyboardUp } = setup({ homeScreenApp: true });
      doc.activeElement = INPUT;
      heights.frame = 793;
      keyboardUp(483, 310);
      advance(5000);
      keyboardUp(793, 0);
      advance(0);
      expect(lifts()).toHaveLength(1);
    });

    it("drops a lift that is pending when the keyboard rises before it runs", () => {
      const { doc, heights, advance, lifts, keyboardUp } = setup({ homeScreenApp: true });
      doc.activeElement = INPUT;
      heights.frame = 793;
      heights.screen = 852;
      keyboardUp(483, 310);
      advance(0);
      expect(lifts()).toHaveLength(0);
    });

    it("restores a frame that turns short with no keyboard change", () => {
      const { heights, win, flush, advance, lifts } = setup({ homeScreenApp: true });
      advance(5000);
      expect(lifts()).toHaveLength(0);
      heights.frame = 793;
      win.fire("resize");
      flush();
      advance(0);
      expect(lifts()).toHaveLength(1);
    });
  });

  describe("stopping", () => {
    it("removes its boxes, cancels what is pending, publishes the keyboard closed and stops listening", () => {
      const { boxes, doc, heights, published, stop, timers, frames, win, keyboardUp, flush } = setup({ homeScreenApp: true });
      doc.activeElement = INPUT;
      heights.frame = 793;
      keyboardUp(483, 310);
      keyboardUp(793, 0);
      win.fire("resize");
      stop();
      expect(boxes.every((box) => box.removed)).toBe(true);
      expect(timers.size).toBe(0);
      expect(frames.size).toBe(0);
      expect(published.at(-1)).toEqual(KEYBOARD_CLOSED);
      const count = published.length;
      keyboardUp(483, 310);
      flush();
      expect(published).toHaveLength(count);
    });

    it("publishes closed once and makes no boxes in a browser without a visual viewport", () => {
      const { boxes, published } = setup({ visualViewport: false, homeScreenApp: true });
      expect(published).toEqual([KEYBOARD_CLOSED]);
      expect(boxes).toHaveLength(0);
    });
  });
});
