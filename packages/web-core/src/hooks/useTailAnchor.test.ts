import { createElement, type FocusEvent, type RefObject } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { TailField } from "../tailAnchor";
import { useTailAnchor } from "./useTailAnchor";

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
    render(input).onBlur(event(input));
    expect(input.scrollLeft).toBe(0);
  });
});
