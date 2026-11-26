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
  it("shows exited sessions until they are turned off", () => {
    const storage = memoryStorage();
    expect(readStoredFlag(storage, "k")).toBe(true);
    expect(writeStoredFlag(storage, "k", false)).toBe(true);
    expect(readStoredFlag(storage, "k")).toBe(false);
    expect(writeStoredFlag(storage, "k", true)).toBe(true);
    expect(readStoredFlag(storage, "k")).toBe(true);
    expect(storage.data.size).toBe(0);
  });

  it("keeps each key apart", () => {
    const storage = memoryStorage();
    writeStoredFlag(storage, "panel", false);
    expect(readStoredFlag(storage, "switcher")).toBe(true);
  });

  it("falls back to shown when storage is missing or throws", () => {
    expect(readStoredFlag(null, "k")).toBe(true);
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
    expect(readStoredFlag(broken, "k")).toBe(true);
    expect(writeStoredFlag(broken, "k", false)).toBe(false);
    expect(writeStoredFlag(null, "k", false)).toBe(false);
  });
});

describe("show exited sessions, by default", () => {
  it("v1's default shows them and stores only a hide, exactly as before", () => {
    const storage = memoryStorage();
    expect(readStoredFlag(storage, "k")).toBe(true);
    writeStoredFlag(storage, "k", false);
    expect(storage.data.get("k")).toBe("0");
    expect(readStoredFlag(storage, "k")).toBe(false);
    writeStoredFlag(storage, "k", true);
    expect(storage.data.has("k")).toBe(false);
    expect(readStoredFlag(storage, "k")).toBe(true);
  });

  it("a hidden default starts hidden, remembers a show, and returns to the default when hidden again", () => {
    const storage = memoryStorage();
    expect(readStoredFlag(storage, "k", false)).toBe(false);
    writeStoredFlag(storage, "k", true, false);
    expect(storage.data.get("k")).toBe("1");
    expect(readStoredFlag(storage, "k", false)).toBe(true);
    writeStoredFlag(storage, "k", false, false);
    expect(storage.data.has("k")).toBe(false);
    expect(readStoredFlag(storage, "k", false)).toBe(false);
  });

  it("a stored choice wins over either default", () => {
    const storage = memoryStorage();
    storage.setItem("k", "0");
    expect(readStoredFlag(storage, "k", true)).toBe(false);
    storage.setItem("k", "1");
    expect(readStoredFlag(storage, "k", false)).toBe(true);
  });

  it("falls back to the default when storage is missing or throws", () => {
    expect(readStoredFlag(null, "k", false)).toBe(false);
    const broken = {
      getItem: () => {
        throw new Error("denied");
      },
    };
    expect(readStoredFlag(broken, "k", false)).toBe(false);
    expect(readStoredFlag(broken, "k")).toBe(true);
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
