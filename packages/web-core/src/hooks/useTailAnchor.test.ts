import { createElement, createRef, type FocusEvent, type PointerEvent } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TailField } from "../tailAnchor";
import { useTailAnchor } from "./useTailAnchor";

// A server render runs no effects; the hook's two are captured to run by hand.
const captured = vi.hoisted(() => ({
  layout: [] as { effect: () => void; deps: unknown[] }[],
  handle: [] as { ref: unknown; init: () => unknown; deps: unknown[] }[],
}));
vi.mock("react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react")>()),
  useLayoutEffect: (effect: () => void, deps: unknown[]) => void captured.layout.push({ effect, deps }),
  useImperativeHandle: (ref: unknown, init: () => unknown, deps: unknown[]) => void captured.handle.push({ ref, init, deps }),
}));

const VALUE = "/home/ada/Workspace/app-name";

function field(): TailField {
  const state: TailField = {
    value: VALUE,
    scrollLeft: 0,
    scrollWidth: 340,
    selectionStart: 0,
    selectionEnd: 0,
    setSelectionRange(start, end) {
      state.selectionStart = start;
      state.selectionEnd = end;
    },
  };
  return state;
}

type Props = Parameters<typeof useTailAnchor>[1];

function mount(forwarded: Parameters<typeof useTailAnchor>[0], props: Props) {
  let result: ReturnType<typeof useTailAnchor> | undefined;
  function Probe() {
    result = useTailAnchor(forwarded, props);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return result as ReturnType<typeof useTailAnchor>;
}

const focusEvent = (target: TailField) => ({ currentTarget: target }) as unknown as FocusEvent<HTMLInputElement>;
const pointerEvent = () => ({}) as PointerEvent<HTMLInputElement>;

describe("useTailAnchor", () => {
  beforeEach(() => {
    captured.layout.length = 0;
    captured.handle.length = 0;
    vi.stubGlobal("document", { activeElement: null });
    vi.stubGlobal("requestAnimationFrame", (callback: () => void) => {
      callback();
      return 0;
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("moves the caret to the end before the caller's focus handler runs", () => {
    const input = field();
    let caret: number | null = null;
    const onFocus = vi.fn(() => void (caret = input.selectionStart));
    mount(undefined, { value: VALUE, onFocus }).onFocus(focusEvent(input));
    expect(onFocus).toHaveBeenCalledOnce();
    expect(caret).toBe(VALUE.length);
    expect(input.scrollLeft).toBe(340);
  });

  it("leaves the caret where a press put it, and still calls the caller's press handler", () => {
    const input = field();
    const onPointerDown = vi.fn();
    const handlers = mount(undefined, { value: VALUE, onPointerDown });
    handlers.onPointerDown(pointerEvent());
    handlers.onFocus(focusEvent(input));
    expect(onPointerDown).toHaveBeenCalledOnce();
    expect([input.selectionStart, input.scrollLeft]).toEqual([0, 0]);
  });

  it("shows the end on blur, and calls the caller's blur handler", () => {
    const input = field();
    const onBlur = vi.fn();
    mount(undefined, { value: VALUE, onBlur }).onBlur(focusEvent(input));
    expect(onBlur).toHaveBeenCalledOnce();
    expect(input.scrollLeft).toBe(340);
  });

  it("hands the input to the forwarded ref", () => {
    const input = field() as unknown as HTMLInputElement;
    const forwarded = createRef<HTMLInputElement>();
    const handlers = mount(forwarded, { value: VALUE });
    handlers.ref.current = input;
    expect(captured.handle[0].ref).toBe(forwarded);
    expect(captured.handle[0].init()).toBe(input);
  });

  it("shows the end after mount and again when the value changes", () => {
    const input = field();
    const first = mount(undefined, { value: "a" });
    const second = mount(undefined, { value: "b" });
    expect(captured.layout[0].deps).toContain("a");
    expect(captured.layout[1].deps).toContain("b");
    expect(captured.layout[0].deps).not.toEqual(captured.layout[1].deps);

    first.ref.current = input as unknown as HTMLInputElement;
    captured.layout[0].effect();
    expect(input.scrollLeft).toBe(340);
    expect(second.ref.current).toBeNull();
  });
});
