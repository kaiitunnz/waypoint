import { createElement, type MouseEvent, type PointerEvent, type TouchEvent } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HOLD_MS } from "./useHoldMenu";
import { useLongPress } from "./useLongPress";

// The hook keeps its state in refs, so the handlers from one server render are enough.
function press(onLongPress: () => void) {
  let handlers: ReturnType<typeof useLongPress> | undefined;
  function Probe() {
    handlers = useLongPress(onLongPress);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return handlers as ReturnType<typeof useLongPress>;
}

const down = { pointerType: "touch", clientX: 0, clientY: 0, currentTarget: {} } as unknown as PointerEvent;
const moved = (clientX: number) => ({ clientX, clientY: 0 }) as PointerEvent;
const release = () => ({ cancelable: true, preventDefault: vi.fn() });
const click = () => ({ preventDefault: vi.fn(), stopPropagation: vi.fn() });

describe("useLongPress", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal("window", globalThis);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  it("refuses the release of a long press and leaves a later click alone", () => {
    const onLongPress = vi.fn();
    const handlers = press(onLongPress);
    handlers.onPointerDown(down);
    vi.advanceTimersByTime(HOLD_MS);
    expect(onLongPress).toHaveBeenCalledOnce();

    const lifted = release();
    handlers.onTouchEnd(lifted as unknown as TouchEvent);
    expect(lifted.preventDefault).toHaveBeenCalledOnce();

    const later = click();
    handlers.onClickCapture(later as unknown as MouseEvent);
    expect(later.preventDefault).not.toHaveBeenCalled();
  });

  it("swallows the click of a long press whose release it never saw", () => {
    const handlers = press(vi.fn());
    handlers.onPointerDown(down);
    vi.advanceTimersByTime(HOLD_MS);
    const swallowed = click();
    handlers.onClickCapture(swallowed as unknown as MouseEvent);
    expect(swallowed.preventDefault).toHaveBeenCalledOnce();
    expect(swallowed.stopPropagation).toHaveBeenCalledOnce();
  });

  it.each([
    ["a drift past the slop", (handlers: ReturnType<typeof useLongPress>) => handlers.onPointerMove(moved(11))],
    ["a scroll taking the touch over", (handlers: ReturnType<typeof useLongPress>) => handlers.onPointerCancel()],
  ])("gives up the hold on %s", (_, interrupt) => {
    const onLongPress = vi.fn();
    const handlers = press(onLongPress);
    handlers.onPointerDown(down);
    interrupt(handlers);
    vi.advanceTimersByTime(HOLD_MS);
    expect(onLongPress).not.toHaveBeenCalled();
  });

  it("holds through a drift within the slop", () => {
    const onLongPress = vi.fn();
    const handlers = press(onLongPress);
    handlers.onPointerDown(down);
    handlers.onPointerMove(moved(10));
    vi.advanceTimersByTime(HOLD_MS);
    expect(onLongPress).toHaveBeenCalledOnce();
  });

  it("ignores a mouse", () => {
    const onLongPress = vi.fn();
    press(onLongPress).onPointerDown({ ...down, pointerType: "mouse" } as PointerEvent);
    vi.advanceTimersByTime(HOLD_MS);
    expect(onLongPress).not.toHaveBeenCalled();
  });

  it("leaves a tap alone", () => {
    const onLongPress = vi.fn();
    const handlers = press(onLongPress);
    handlers.onPointerDown(down);
    vi.advanceTimersByTime(HOLD_MS - 1);
    handlers.onPointerUp();
    const lifted = release();
    handlers.onTouchEnd(lifted as unknown as TouchEvent);
    vi.advanceTimersByTime(HOLD_MS);
    expect(onLongPress).not.toHaveBeenCalled();
    expect(lifted.preventDefault).not.toHaveBeenCalled();
  });
});
