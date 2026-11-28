import { describe, expect, it } from "vitest";

import { createFrameRestore, frameIsShort } from "./frameRestore";

function setup() {
  const state = { short: true, lifted: 0, released: 0 };
  let now = 0;
  let next = 1;
  const timers = new Map<number, { at: number; callback: () => void }>();
  const env = {
    isShort: () => state.short,
    lift: () => {
      state.lifted += 1;
      return () => {
        state.released += 1;
      };
    },
    setTimeout: (callback: () => void, ms: number) => {
      const handle = next++;
      timers.set(handle, { at: now + ms, callback });
      return handle;
    },
    clearTimeout: (handle: number) => {
      timers.delete(handle);
    },
  };
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
  return { state, advance, restore: createFrameRestore(env), pending: () => timers.size };
}

describe("frameIsShort", () => {
  it("is false for a frame that fills the screen, to the rounding", () => {
    expect(frameIsShort(852, 852)).toBe(false);
    expect(frameIsShort(852, 846)).toBe(false);
  });

  it("is true for the status bar's height left over, as an iOS home-screen app can leave it", () => {
    expect(frameIsShort(852, 793)).toBe(true);
  });
});

describe("createFrameRestore", () => {
  it("does nothing while the frame is not short", () => {
    const { state, advance, restore } = setup();
    state.short = false;
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(0);
  });

  it("lifts the document at once", () => {
    const { state, advance, restore } = setup();
    restore.check();
    expect(state.lifted).toBe(0);
    advance(0);
    expect(state.lifted).toBe(1);
    expect(state.released).toBe(0);
  });

  it("lets go as soon as the frame is back", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(0);
    state.short = false;
    advance(60);
    expect(state.released).toBe(1);
    advance(5000);
    expect(state.lifted).toBe(1);
  });

  it("tries a second time when the first lift never brings the frame back, then gives up", () => {
    const { state, advance, restore, pending } = setup();
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(2);
    expect(state.released).toBe(2);
    expect(pending()).toBe(0);
  });

  it("does not try a third time", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(5000);
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(2);
  });

  it("does not lift again, nor lose its poll, when checked while lifted and still short", () => {
    const { state, advance, restore, pending } = setup();
    restore.check();
    advance(0);
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(2);
    expect(state.released).toBe(2);
    expect(pending()).toBe(0);
  });

  it("tries again for a new shortfall once the old one has cleared", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(5000);
    state.short = false;
    restore.check();
    state.short = true;
    restore.check();
    advance(0);
    expect(state.lifted).toBe(3);
  });

  it("tries again for a later shortfall that no check saw clear", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(0);
    state.short = false;
    advance(60);
    state.short = true;
    restore.check();
    advance(0);
    expect(state.lifted).toBe(2);
  });

  it("drops a pending try when the shortfall clears before it runs", () => {
    const { state, advance, restore } = setup();
    restore.check();
    state.short = false;
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(0);
  });

  it("does not lift when the frame is back by the time the try runs", () => {
    const { state, advance, restore } = setup();
    restore.check();
    state.short = false;
    advance(0);
    expect(state.lifted).toBe(0);
  });

  it("releases and stops on dispose", () => {
    const { state, advance, restore, pending } = setup();
    restore.check();
    advance(0);
    restore.dispose();
    expect(state.released).toBe(1);
    expect(pending()).toBe(0);
  });
});
