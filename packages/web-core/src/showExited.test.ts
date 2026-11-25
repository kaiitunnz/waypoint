import { describe, expect, it } from "vitest";

import { readShowExited, writeShowExited } from "./showExited";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    data,
  };
}

describe("show exited sessions", () => {
  it("shows exited sessions until they are turned off", () => {
    const storage = memoryStorage();
    expect(readShowExited(storage, "k")).toBe(true);
    expect(writeShowExited(storage, "k", false)).toBe(true);
    expect(readShowExited(storage, "k")).toBe(false);
    expect(writeShowExited(storage, "k", true)).toBe(true);
    expect(readShowExited(storage, "k")).toBe(true);
    expect(storage.data.size).toBe(0);
  });

  it("keeps each key apart", () => {
    const storage = memoryStorage();
    writeShowExited(storage, "panel", false);
    expect(readShowExited(storage, "switcher")).toBe(true);
  });

  it("falls back to shown when storage is missing or throws", () => {
    expect(readShowExited(null, "k")).toBe(true);
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
      setItem: () => {
        throw new Error("denied");
      },
      removeItem: () => {
        throw new Error("denied");
      },
    };
    expect(readShowExited(broken, "k")).toBe(true);
    expect(writeShowExited(broken, "k", false)).toBe(false);
    expect(writeShowExited(null, "k", false)).toBe(false);
  });
});
