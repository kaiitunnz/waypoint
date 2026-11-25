"use client";

import { LIST_SHOW_EXITED_KEY, useShowExitedSessions } from "@waypoint/web-core/hooks/useShowExitedSessions";

// Whether the session lists show their Exited group: one remembered choice for the home, the
// rail and the switcher, collapsed until it is first used.
export function useShowExited(): [boolean, (next: boolean) => void] {
  return useShowExitedSessions(LIST_SHOW_EXITED_KEY, false);
}
