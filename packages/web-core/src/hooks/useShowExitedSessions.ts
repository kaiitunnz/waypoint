"use client";

import { useStoredFlag } from "./useStoredFlag";

// An unset key means the surface's own default, so surfaces with different defaults keep separate keys.
export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";

// Persisted show-exited flag, `shownByDefault` until a choice is made. Every surface using one key
// agrees, in this tab and across tabs.
export function useShowExitedSessions(storageKey: string, shownByDefault = true): [boolean, (next: boolean) => void] {
  return useStoredFlag(storageKey, shownByDefault);
}
