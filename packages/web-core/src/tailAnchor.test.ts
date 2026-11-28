import { beforeEach, describe, expect, it } from "vitest";

import { createTailAnchor, type TailField } from "./tailAnchor";

const VALUE = "/home/ada/Workspace/app-name";

function field(overrides: Partial<TailField> = {}): TailField {
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
    ...overrides,
  };
  return state;
}

describe("createTailAnchor", () => {
  let focused: TailField | null;
  let frames: (() => void)[];
  let anchor: ReturnType<typeof createTailAnchor<TailField>>;
  const flush = () => frames.splice(0).forEach((frame) => frame());

  beforeEach(() => {
    focused = null;
    frames = [];
    anchor = createTailAnchor({ isFocused: (input) => input === focused, nextFrame: (callback) => frames.push(callback) });
  });

  describe("sync", () => {
    it("shows the end of a field that is not focused", () => {
      const input = field();
      anchor.sync(input);
      expect(input.scrollLeft).toBe(340);
    });

    it("shows the end of a focused field whose caret is at the end", () => {
      const input = field({ selectionStart: VALUE.length, selectionEnd: VALUE.length });
      focused = input;
      anchor.sync(input);
      expect(input.scrollLeft).toBe(340);
    });

    it("leaves a focused field alone while the caret is elsewhere or a range is selected", () => {
      const middle = field({ selectionStart: 3, selectionEnd: 3 });
      const range = field({ selectionStart: 0, selectionEnd: VALUE.length });
      focused = middle;
      anchor.sync(middle);
      focused = range;
      anchor.sync(range);
      expect([middle.scrollLeft, range.scrollLeft]).toEqual([0, 0]);
    });

    it("has nothing to scroll before the field mounts", () => {
      expect(() => anchor.sync(null)).not.toThrow();
    });
  });

  describe("focus", () => {
    it("puts the caret at the end and shows it when focus arrives without a press", () => {
      const input = field();
      anchor.focus(input);
      expect([input.selectionStart, input.selectionEnd]).toEqual([VALUE.length, VALUE.length]);
      expect(input.scrollLeft).toBe(340);
    });

    it("shows the end again once the focus has settled", () => {
      const input = field();
      anchor.focus(input);
      input.scrollLeft = 0;
      flush();
      expect(input.scrollLeft).toBe(340);
    });

    it("keeps a selection, as a Tab into the field leaves one", () => {
      const input = field({ selectionStart: 0, selectionEnd: VALUE.length });
      anchor.focus(input);
      expect([input.selectionStart, input.selectionEnd]).toEqual([0, VALUE.length]);
      expect(input.scrollLeft).toBe(340);
    });

    it("leaves the caret and the scroll where a press put them", () => {
      const input = field();
      anchor.press();
      anchor.focus(input);
      flush();
      expect([input.selectionStart, input.scrollLeft]).toEqual([0, 0]);
    });

    it("treats the focus after a blur as unpressed again", () => {
      const input = field();
      anchor.press();
      anchor.blur(input);
      anchor.focus(input);
      expect(input.selectionStart).toBe(VALUE.length);
    });
  });

  describe("blur", () => {
    it("shows the end a frame later", () => {
      const input = field();
      anchor.blur(input);
      expect(input.scrollLeft).toBe(0);
      flush();
      expect(input.scrollLeft).toBe(340);
    });

    it("leaves a field that has taken the focus back alone", () => {
      const input = field();
      anchor.blur(input);
      focused = input;
      flush();
      expect(input.scrollLeft).toBe(0);
    });
  });
});
