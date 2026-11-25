// v1's panel and switcher show exited sessions unless turned off.
export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";
// v2's lists, which start with them hidden, share this one: its default differs, so it has its own key.
export const LIST_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.list";

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "removeItem" | "setItem">;

// The stored choice, or `fallback` when none was made. Only the choice that differs from the
// default is stored, so a stored "0" always means hidden and "1" shown.
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
