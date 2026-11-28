import { describe, expect, it } from "vitest";

import { createFrameRestore } from "./frameRestore";

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

describe("createFrameRestore", () => {
  it("does nothing while the frame is not short", () => {
    const { state, advance, restore } = setup();
    state.short = false;
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(0);
  });

  it("waits for the keyboard to settle, then lifts the document", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(100);
    expect(state.lifted).toBe(0);
    advance(150);
    expect(state.lifted).toBe(1);
    expect(state.released).toBe(0);
  });

  it("lets go as soon as the frame is back", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(250);
    state.short = false;
    advance(60);
    expect(state.released).toBe(1);
  });

  it("lets go after a bounded wait when the frame never comes back", () => {
    const { state, advance, restore, pending } = setup();
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(1);
    expect(state.released).toBe(1);
    expect(pending()).toBe(0);
  });

  it("tries once per shortfall, and again after the shortfall has cleared", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(5000);
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(1);
    state.short = false;
    restore.check();
    state.short = true;
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(2);
  });

  it("drops a pending try when the shortfall clears while settling", () => {
    const { state, advance, restore } = setup();
    restore.check();
    advance(100);
    state.short = false;
    restore.check();
    advance(5000);
    expect(state.lifted).toBe(0);
  });

  it("does not lift when the frame is back by the time it settles", () => {
    const { state, advance, restore } = setup();
    restore.check();
    state.short = false;
    advance(250);
    expect(state.lifted).toBe(0);
  });

  it("releases and stops on dispose", () => {
    const { state, advance, restore, pending } = setup();
    restore.check();
    advance(250);
    restore.dispose();
    expect(state.released).toBe(1);
    expect(pending()).toBe(0);
  });
});
