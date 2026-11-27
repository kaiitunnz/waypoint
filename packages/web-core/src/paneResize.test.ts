import { describe, expect, it } from "vitest";

import { clampPaneWidth, snapPaneWidth } from "./paneResize";

const config = { storageKey: "k", defaultWidth: 340, min: 260, max: 560, snap: 14 };

describe("clampPaneWidth", () => {
  it("keeps the width in range and within half the window", () => {
    expect(clampPaneWidth(100, config)).toBe(260);
    expect(clampPaneWidth(900, config)).toBe(560);
    expect(clampPaneWidth(500, config, 800)).toBe(400);
    expect(clampPaneWidth(300, config, 300)).toBe(260);
  });
});

describe("snapPaneWidth", () => {
  it("forgets a width near the default", () => {
    expect(snapPaneWidth(354, config)).toBeUndefined();
    expect(snapPaneWidth(326, config)).toBeUndefined();
    expect(snapPaneWidth(355, config)).toBe(355);
    expect(snapPaneWidth(900, config, 1440)).toBe(560);
  });
});
