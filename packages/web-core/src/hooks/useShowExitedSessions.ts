"use client";

import { useStoredFlag } from "./useStoredFlag";

// An unset key means the surface's own default, so surfaces with different defaults keep separate keys.
export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";
export const LIST_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.list";

// Persisted show-exited flag, `shownByDefault` until a choice is made. Every surface using one key
// agrees wherever they share its storage: the browser's by default.
export function useShowExitedSessions(
  storageKey: string,
  shownByDefault = true,
  storage?: () => Storage | null,
): [boolean, (next: boolean) => void] {
  return useStoredFlag(storageKey, shownByDefault, storage);
}
