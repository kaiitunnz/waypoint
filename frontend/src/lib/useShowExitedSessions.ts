"use client";

import { useCallback, useEffect, useState } from "react";

export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";

function readShowExited(storageKey: string): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  return window.localStorage.getItem(storageKey) !== "0";
}

// Persisted show-exited flag for one surface; default on. Synced across tabs.
export function useShowExitedSessions(storageKey: string): [boolean, (next: boolean) => void] {
  // Init true and hydrate in the effect to avoid an SSR/client hydration mismatch.
  const [showExited, setShowExited] = useState(true);

  useEffect(() => {
    setShowExited(readShowExited(storageKey));
    function handleStorage(event: StorageEvent) {
      if (event.key === storageKey) {
        setShowExited(readShowExited(storageKey));
      }
    }
    window.addEventListener("storage", handleStorage);
    return () => window.removeEventListener("storage", handleStorage);
  }, [storageKey]);

  const update = useCallback(
    (next: boolean) => {
      if (typeof window !== "undefined") {
        if (next) {
          window.localStorage.removeItem(storageKey);
        } else {
          window.localStorage.setItem(storageKey, "0");
        }
      }
      setShowExited(next);
    },
    [storageKey],
  );

  return [showExited, update];
}
