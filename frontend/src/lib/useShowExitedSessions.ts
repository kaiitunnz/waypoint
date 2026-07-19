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

// Client preference shared by the sessions panel and the session switcher: a
// single persisted flag kept live across both surfaces (and across tabs) so a
// toggle on one reflects on the other. Default ON preserves the prior
// behavior of showing exited sessions.
export function useShowExitedSessions(): [boolean, (next: boolean) => void] {
  // Init true and hydrate in the effect to avoid an SSR/client hydration
  // mismatch; the only visible cost is a one-frame flash when the stored
  // preference is the non-default "hidden".
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
