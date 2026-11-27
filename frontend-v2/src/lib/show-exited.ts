"use client";

import { LIST_SHOW_EXITED_KEY } from "@waypoint/web-core/hooks/useShowExitedSessions";
import { useStoredFlag } from "@waypoint/web-core/hooks/useStoredFlag";
import { windowStorage } from "@waypoint/web-core/storedFlag";

// Whether the session lists show their Exited group: one choice per window for the home,
// the rail and the switcher, collapsed in a new window.
export function useShowExited(): [boolean, (next: boolean) => void] {
  return useStoredFlag(LIST_SHOW_EXITED_KEY, false, windowStorage);
}
