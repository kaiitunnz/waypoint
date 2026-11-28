import { describe, expect, it } from "vitest";

import { caretToEnd, showEnd, type TailField } from "./tailAnchor";

function field(overrides: Partial<TailField> = {}): TailField {
  const state: TailField = {
    value: "/home/noppanat/Workspace/Project/FlowMesh-v2",
    scrollLeft: 0,
    scrollWidth: 340,
    selectionStart: 0,
    selectionEnd: 0,
    setSelectionRange(start, end) {
      state.selectionStart = start;
      state.selectionEnd = end;
    },
    ...overrides,
  };
  return state;
}

describe("showEnd", () => {
  it("scrolls the field to its end", () => {
    const input = field();
    showEnd(input);
    expect(input.scrollLeft).toBe(340);
  });
});

describe("caretToEnd", () => {
  it("puts a collapsed caret at the end of the value and shows it", () => {
    const input = field();
    caretToEnd(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([input.value.length, input.value.length]);
    expect(input.scrollLeft).toBe(340);
  });

  it("keeps a selection, as a Tab into the field leaves one", () => {
    const input = field({ selectionStart: 0, selectionEnd: 44 });
    caretToEnd(input);
    expect([input.selectionStart, input.selectionEnd]).toEqual([0, 44]);
    expect(input.scrollLeft).toBe(340);
  });
});
