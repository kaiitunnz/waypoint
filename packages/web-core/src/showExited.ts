export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";

type ReadableStorage = Pick<Storage, "getItem">;
type WritableStorage = Pick<Storage, "removeItem" | "setItem">;

// Exited sessions show unless the person turned them off.
export function readShowExited(storage: ReadableStorage | null, key: string): boolean {
  try {
    return storage?.getItem(key) !== "0";
  } catch {
    return true;
  }
}

export function writeShowExited(storage: WritableStorage | null, key: string, show: boolean): void {
  try {
    if (show) storage?.removeItem(key);
    else storage?.setItem(key, "0");
  } catch {
    // Storage can be unavailable (private windows); the choice then lasts until reload.
  }
}
