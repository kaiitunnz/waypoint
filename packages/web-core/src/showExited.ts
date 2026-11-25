type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "removeItem" | "setItem">;

// Only a choice that differs from `fallback` is stored, so a stored "0" always means hidden and "1" shown.
export function readShowExited(storage: ReadableStorage | null, key: string, fallback = true): boolean {
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
export function writeShowExited(
  storage: WritableStorage | null,
  key: string,
  show: boolean,
  fallback = true,
): boolean {
  if (!storage) return false;
  try {
    if (show === fallback) storage.removeItem(key);
    else storage.setItem(key, show ? "1" : "0");
    return true;
  } catch {
    return false;
  }
}
