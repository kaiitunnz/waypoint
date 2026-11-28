import { createElement, type FocusEvent, type RefObject } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TailField } from "../tailAnchor";
import { useTailAnchor } from "./useTailAnchor";

// A server render skips layout effects; run them as the render goes.
vi.mock("react", async (importOriginal) => ({
  ...(await importOriginal<typeof import("react")>()),
  useLayoutEffect: (effect: () => void) => effect(),
}));

type Field = TailField & { id: string };

function field(): Field {
  const state: Field = {
    id: "folder",
    value: "/home/noppanat/Workspace/Project/FlowMesh-v2",
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

// The hook keeps its state in refs, so the handlers from one server render are enough.
function render(input: Field | null, value = "") {
  let handlers: ReturnType<typeof useTailAnchor> | undefined;
  const ref = { current: input } as unknown as RefObject<HTMLInputElement | null>;
  function Probe() {
    handlers = useTailAnchor(ref, value);
    return null;
  }
  renderToStaticMarkup(createElement(Probe));
  return handlers as ReturnType<typeof useTailAnchor>;
}

const event = (target: Field) => ({ currentTarget: target }) as unknown as FocusEvent<HTMLInputElement>;

describe("useTailAnchor", () => {
  let active: Field | null;

  beforeEach(() => {
    active = null;
    vi.stubGlobal("document", {
      get activeElement() {
        return active;
      },
    });
    vi.stubGlobal("requestAnimationFrame", (callback: () => void) => {
      callback();
      return 0;
    });
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("puts the caret at the end and shows it when focus arrives without a press", () => {
    const input = field();
    render(input).onFocus(event(input));
    expect([input.selectionStart, input.selectionEnd]).toEqual([input.value.length, input.value.length]);
    expect(input.scrollLeft).toBe(340);
  });

  it("leaves the caret and the scroll where a press put them", () => {
    const input = field();
    const handlers = render(input);
    input.scrollLeft = 0;
    handlers.onPointerDown();
    handlers.onFocus(event(input));
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 0]);
    expect(input.scrollLeft).toBe(0);
  });

  it("treats the focus after a blur as unpressed again", () => {
    const input = field();
    const handlers = render(input);
    handlers.onPointerDown();
    handlers.onBlur(event(input));
    handlers.onFocus(event(input));
    expect(input.selectionStart).toBe(input.value.length);
  });

  it("shows the end after a blur", () => {
    const input = field();
    render(input).onBlur(event(input));
    expect(input.scrollLeft).toBe(340);
  });

  it("leaves a field that has taken the focus back alone after a blur", () => {
    const input = field();
    active = input;
    const handlers = render(input);
    input.scrollLeft = 0;
    handlers.onBlur(event(input));
    expect(input.scrollLeft).toBe(0);
  });

  describe("when its value is set", () => {
    it("shows the end of a field that is not focused", () => {
      const input = field();
      render(input, input.value);
      expect(input.scrollLeft).toBe(340);
    });

    it("shows the end of a focused field whose caret is at the end", () => {
      const input = field();
      input.selectionStart = input.selectionEnd = input.value.length;
      active = input;
      render(input, input.value);
      expect(input.scrollLeft).toBe(340);
    });

    it("leaves a focused field alone while the caret is elsewhere", () => {
      const input = field();
      input.selectionStart = input.selectionEnd = 3;
      active = input;
      render(input, input.value);
      expect(input.scrollLeft).toBe(0);
    });

    it("has nothing to scroll before the field mounts", () => {
      expect(() => render(null)).not.toThrow();
    });
  });
});
