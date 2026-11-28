import { describe, expect, it } from "vitest";

import { scrollDelta } from "./revealField";

describe("scrollDelta", () => {
  const view = { top: 0, bottom: 500 };

  it("is 0 for a field already in view", () => {
    expect(scrollDelta({ top: 100, bottom: 144 }, view)).toBe(0);
  });

  it("scrolls down by what lies below the view, plus a margin", () => {
    expect(scrollDelta({ top: 520, bottom: 564 }, view)).toBe(72);
  });

  it("scrolls up by what lies above the view, plus a margin", () => {
    expect(scrollDelta({ top: -40, bottom: 4 }, view)).toBe(-48);
  });

  it("lines up the top of a field taller than the view", () => {
    expect(scrollDelta({ top: 200, bottom: 900 }, view)).toBe(200);
  });
});
