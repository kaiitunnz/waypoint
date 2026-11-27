import { createElement, type MouseEvent, type PointerEvent, type TouchEvent } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { HOLD_MS } from "./useHoldMenu";
import { useLongPress } from "./useLongPress";

// The handlers of one rendered hook; they are all the hook does between renders.
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

  it("refuses the release of a long press, and no click after it", () => {
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
