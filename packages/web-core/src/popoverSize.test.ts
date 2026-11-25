import { afterEach, describe, expect, it, vi } from "vitest";

import {
  clampSize,
  keyResizeDelta,
  parsePopoverSize,
  readPopoverSize,
  resizedDimension,
  writePopoverSize,
} from "./popoverSize";

function stubStorage(initial: Record<string, string> = {}) {
  const values = new Map(Object.entries(initial));
  vi.stubGlobal("window", {
    localStorage: {
      getItem: (key: string) => values.get(key) ?? null,
      setItem: (key: string, value: string) => void values.set(key, value),
      removeItem: (key: string) => void values.delete(key),
    },
  });
  return values;
}

afterEach(() => vi.unstubAllGlobals());

describe("parsePopoverSize", () => {
  it("keeps positive finite dimensions only", () => {
    expect(parsePopoverSize('{"width":520,"height":300}')).toEqual({ width: 520, height: 300 });
    expect(parsePopoverSize('{"width":-1,"height":"300"}')).toEqual({});
    expect(parsePopoverSize('{"height":260}')).toEqual({ height: 260 });
  });

  it("treats missing or malformed values as no preference", () => {
    expect(parsePopoverSize(null)).toEqual({});
    expect(parsePopoverSize("not json")).toEqual({});
    expect(parsePopoverSize("null")).toEqual({});
    expect(parsePopoverSize("3")).toEqual({});
  });
});

describe("stored popover size", () => {
  it("round-trips a size and drops the entry once no axis is set", () => {
    const values = stubStorage();
    writePopoverSize("k", { width: 500 });
    expect(values.get("k")).toBe('{"width":500}');
    expect(readPopoverSize("k")).toEqual({ width: 500 });
    writePopoverSize("k", { width: undefined, height: undefined });
    expect(values.has("k")).toBe(false);
  });

  it("is best-effort when storage throws", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => {
          throw new Error("denied");
        },
        setItem: () => {
          throw new Error("quota");
        },
      },
    });
    expect(readPopoverSize("k")).toEqual({});
    expect(() => writePopoverSize("k", { width: 1 })).not.toThrow();
  });

  it("is empty without a window", () => {
    expect(readPopoverSize("k")).toEqual({});
    expect(() => writePopoverSize("k", { width: 1 })).not.toThrow();
  });
});

describe("resizing", () => {
  it("clamps to the bounds", () => {
    expect(clampSize(100, 320, 800)).toBe(320);
    expect(clampSize(900, 320, 800)).toBe(800);
    expect(clampSize(500, 320, 800)).toBe(500);
  });

  it("returns an axis to its default near the detent", () => {
    expect(resizedDimension(452, 320, 800, 440)).toBeUndefined();
    expect(resizedDimension(426, 320, 800, 440)).toBeUndefined();
    expect(resizedDimension(455, 320, 800, 440)).toBe(455);
    expect(resizedDimension(900, 320, 800, 440)).toBe(800);
    expect(resizedDimension(500, 320, 800, null)).toBe(500);
  });

  it("grows toward the free edge", () => {
    const upLeft = { width: -1, height: -1 } as const;
    expect(keyResizeDelta("width", "ArrowLeft", false, upLeft)).toBe(24);
    expect(keyResizeDelta("width", "ArrowRight", false, upLeft)).toBe(-24);
    expect(keyResizeDelta("height", "ArrowUp", true, upLeft)).toBe(72);
    const downRight = { width: 1, height: 1 } as const;
    expect(keyResizeDelta("width", "ArrowRight", false, downRight)).toBe(24);
    expect(keyResizeDelta("height", "ArrowUp", false, downRight)).toBe(-24);
  });

  it("ignores keys for the other axis", () => {
    const direction = { width: -1, height: -1 } as const;
    expect(keyResizeDelta("width", "ArrowUp", false, direction)).toBeNull();
    expect(keyResizeDelta("height", "ArrowLeft", false, direction)).toBeNull();
    expect(keyResizeDelta("height", "Enter", false, direction)).toBeNull();
  });
});
