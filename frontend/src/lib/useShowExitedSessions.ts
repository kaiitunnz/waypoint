"use client";

import { useCallback, useEffect, useState } from "react";

const STORAGE_KEY = "waypoint.show-exited-sessions";
const CHANGE_EVENT = "waypoint:show-exited-sessions-change";

function readShowExited(): boolean {
  if (typeof window === "undefined") {
    return true;
  }
  return window.localStorage.getItem(STORAGE_KEY) !== "0";
}

// Persisted flag synced across the panel, the switcher, and tabs; default on.
export function useShowExitedSessions(): [boolean, (next: boolean) => void] {
  // Init true and hydrate in the effect to avoid an SSR/client hydration mismatch.
  const [showExited, setShowExited] = useState(true);

  useEffect(() => {
    setShowExited(readShowExited());
    function handleChange() {
      setShowExited(readShowExited());
    }
    window.addEventListener(CHANGE_EVENT, handleChange);
    window.addEventListener("storage", handleChange);
    return () => {
      window.removeEventListener(CHANGE_EVENT, handleChange);
      window.removeEventListener("storage", handleChange);
    };
  }, []);

  const update = useCallback((next: boolean) => {
    if (typeof window !== "undefined") {
      if (next) {
        window.localStorage.removeItem(STORAGE_KEY);
      } else {
        window.localStorage.setItem(STORAGE_KEY, "0");
      }
      window.dispatchEvent(new Event(CHANGE_EVENT));
    }
    setShowExited(next);
  }, []);

  return [showExited, update];
}
