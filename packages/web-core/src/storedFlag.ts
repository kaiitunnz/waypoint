type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "removeItem" | "setItem">;

// Only a choice that differs from `fallback` is stored, so a stored "0" always means off and "1" on.
export function readStoredFlag(storage: ReadableStorage | null, key: string, fallback: boolean): boolean {
  try {
    const stored = storage?.getItem(key);
    if (stored === "0") return false;
    if (stored === "1") return true;
    return fallback;
  } catch {
    return fallback;
  }
}

// Whether the choice was stored; storage can be unavailable (private windows, a full quota).
export function writeStoredFlag(storage: WritableStorage | null, key: string, value: boolean, fallback: boolean): boolean {
  if (!storage) return false;
  try {
    if (value === fallback) storage.removeItem(key);
    else storage.setItem(key, value ? "1" : "0");
    return true;
  } catch {
    return false;
  }
}

export interface StoredFlag {
  get: () => boolean;
  set: (value: boolean) => void;
  subscribe: (listener: () => void) => () => void;
}

// Readers of a key in this tab learn of a change through these; other tabs through `storage`.
const sameTab = new Map<string, Set<() => void>>();
// Choices storage refused, kept for the life of the page.
const unsaved = new Map<string, boolean>();

function browserStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.localStorage;
  } catch {
    return null;
  }
}

// Storage of this window alone: it survives a reload, and a new window starts empty.
export function windowStorage(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

// A flag remembered in this browser, or in the `storage` given, `fallback` until a
// choice is made. Every reader of one key agrees wherever they share that storage.
export function storedFlag(key: string, fallback: boolean, storage: () => Storage | null = browserStorage): StoredFlag {
  return {
    get: () => unsaved.get(key) ?? readStoredFlag(storage(), key, fallback),
    set: (value) => {
      if (writeStoredFlag(storage(), key, value, fallback)) unsaved.delete(key);
      else unsaved.set(key, value);
      for (const listener of sameTab.get(key) ?? []) listener();
    },
    subscribe: (listener) => {
      const listeners = sameTab.get(key) ?? new Set();
      sameTab.set(key, listeners.add(listener));
      const onStorage = (event: StorageEvent) => {
        if (event.key === key || event.key === null) listener();
      };
      if (typeof window !== "undefined") window.addEventListener("storage", onStorage);
      return () => {
        listeners.delete(listener);
        if (typeof window !== "undefined") window.removeEventListener("storage", onStorage);
      };
    },
  };
}
