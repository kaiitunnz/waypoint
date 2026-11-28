import { describe, expect, it } from "vitest";

import { caretToEnd, followsEnd, showEnd, type TailField } from "./tailAnchor";

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

describe("followsEnd", () => {
  it("follows the end of a field that is not focused", () => {
    expect(followsEnd(field({ selectionStart: 3, selectionEnd: 3 }), false)).toBe(true);
  });

  it("follows the end of a focused field whose caret is at the end", () => {
    expect(followsEnd(field({ selectionStart: 44, selectionEnd: 44 }), true)).toBe(true);
  });

  it("leaves a focused field alone while the caret is elsewhere or a range is selected", () => {
    expect(followsEnd(field({ selectionStart: 3, selectionEnd: 3 }), true)).toBe(false);
    expect(followsEnd(field({ selectionStart: 0, selectionEnd: 44 }), true)).toBe(false);
  });
});
