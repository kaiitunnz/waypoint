import { afterEach, describe, expect, it, vi } from "vitest";

import { CONVERSATION_PREVIEWS_DEFAULT, CONVERSATION_PREVIEWS_KEY } from "./hooks/useConversationPreviews";
import { readStoredFlag, storedFlag, writeStoredFlag } from "./storedFlag";

function memoryStorage() {
  const data = new Map<string, string>();
  return {
    getItem: (key: string) => data.get(key) ?? null,
    setItem: (key: string, value: string) => void data.set(key, value),
    removeItem: (key: string) => void data.delete(key),
    data,
  };
}

describe("readStoredFlag and writeStoredFlag", () => {
  it("stores only a choice that differs from the fallback", () => {
    for (const fallback of [true, false]) {
      const storage = memoryStorage();
      expect(readStoredFlag(storage, "k", fallback)).toBe(fallback);
      writeStoredFlag(storage, "k", !fallback, fallback);
      expect(storage.data.get("k")).toBe(fallback ? "0" : "1");
      expect(readStoredFlag(storage, "k", fallback)).toBe(!fallback);
      writeStoredFlag(storage, "k", fallback, fallback);
      expect(storage.data.has("k")).toBe(false);
    }
  });

  it("lets a stored choice win over either fallback", () => {
    const storage = memoryStorage();
    storage.setItem("k", "0");
    expect(readStoredFlag(storage, "k", true)).toBe(false);
    storage.setItem("k", "1");
    expect(readStoredFlag(storage, "k", false)).toBe(true);
  });

  it("keeps each key apart", () => {
    const storage = memoryStorage();
    writeStoredFlag(storage, "a", false);
    expect(readStoredFlag(storage, "b")).toBe(true);
  });

  it("falls back when storage is missing or throws", () => {
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
    for (const fallback of [true, false]) {
      expect(readStoredFlag(null, "k", fallback)).toBe(fallback);
      expect(readStoredFlag(broken, "k", fallback)).toBe(fallback);
      expect(writeStoredFlag(broken, "k", !fallback, fallback)).toBe(false);
      expect(writeStoredFlag(null, "k", !fallback, fallback)).toBe(false);
    }
  });
});

describe("storedFlag", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("reads its default, then the stored choice", () => {
    const storage = memoryStorage();
    const flag = storedFlag("a", true, () => storage as unknown as Storage);
    expect(flag.get()).toBe(true);
    flag.set(false);
    expect(flag.get()).toBe(false);
    expect(storage.data.get("a")).toBe("0");
  });

  it("tells every reader of the key in this tab, and no other key", () => {
    const storage = () => memoryStorage() as unknown as Storage;
    const shared = memoryStorage();
    const one = storedFlag("b", true, () => shared as unknown as Storage);
    const two = storedFlag("b", true, () => shared as unknown as Storage);
    const other = storedFlag("c", true, storage);
    const heard = vi.fn();
    const unrelated = vi.fn();
    const stop = two.subscribe(heard);
    other.subscribe(unrelated);
    one.set(false);
    expect(heard).toHaveBeenCalledTimes(1);
    expect(two.get()).toBe(false);
    expect(unrelated).not.toHaveBeenCalled();
    stop();
    one.set(true);
    expect(heard).toHaveBeenCalledTimes(1);
  });

  it("keeps a choice storage refuses for the life of the page", () => {
    const refusing = { getItem: () => null, setItem: () => { throw new Error("full"); }, removeItem: () => {} };
    const flag = storedFlag("d", true, () => refusing as unknown as Storage);
    flag.set(false);
    expect(flag.get()).toBe(false);
  });

  it("hears a change made in another tab", () => {
    const target = new EventTarget();
    vi.stubGlobal("window", target);
    const heard = vi.fn();
    storedFlag("e", true, () => null).subscribe(heard);
    target.dispatchEvent(Object.assign(new Event("storage"), { key: "f" }));
    expect(heard).not.toHaveBeenCalled();
    target.dispatchEvent(Object.assign(new Event("storage"), { key: "e" }));
    expect(heard).toHaveBeenCalledTimes(1);
  });
});

describe("conversation previews", () => {
  it("are on until turned off, and stored under their own key", () => {
    const storage = memoryStorage();
    expect(CONVERSATION_PREVIEWS_KEY).toBe("waypoint.conversation-previews");
    const flag = storedFlag(CONVERSATION_PREVIEWS_KEY, CONVERSATION_PREVIEWS_DEFAULT, () => storage as unknown as Storage);
    expect(flag.get()).toBe(true);
    flag.set(false);
    expect(storage.data.get(CONVERSATION_PREVIEWS_KEY)).toBe("0");
    flag.set(true);
    expect(storage.data.size).toBe(0);
  });
});
