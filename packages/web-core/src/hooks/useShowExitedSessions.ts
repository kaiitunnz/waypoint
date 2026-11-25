"use client";

import { useCallback, useSyncExternalStore } from "react";

import { readShowExited, writeShowExited } from "../showExited";

// An unset key means the surface's own default, so surfaces with different defaults keep separate keys.
export const PANEL_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.panel";
export const SWITCHER_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.switcher";
export const LIST_SHOW_EXITED_KEY = "waypoint.show-exited-sessions.list";

// Surfaces in this tab that share a key learn of a change through this; other tabs through `storage`.
const sameTab = new Set<() => void>();
// Choices storage refused, kept for the life of the page.
const unsaved = new Map<string, boolean>();

function localStorageOrNull(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function subscribe(listener: () => void): () => void {
  sameTab.add(listener);
  window.addEventListener("storage", listener);
  return () => {
    sameTab.delete(listener);
    window.removeEventListener("storage", listener);
  };
}

// Persisted show-exited flag, `shownByDefault` until a choice is made. Every surface using one key
// agrees, in this tab and across tabs.
export function useShowExitedSessions(
  storageKey: string,
  shownByDefault = true,
): [boolean, (next: boolean) => void] {
  const show = useSyncExternalStore(
    subscribe,
    () => unsaved.get(storageKey) ?? readShowExited(localStorageOrNull(), storageKey, shownByDefault),
    () => shownByDefault,
  );
  const update = useCallback(
    (next: boolean) => {
      if (writeShowExited(localStorageOrNull(), storageKey, next, shownByDefault)) unsaved.delete(storageKey);
      else unsaved.set(storageKey, next);
      for (const listener of sameTab) listener();
    },
    [storageKey, shownByDefault],
  );
  return [show, update];
}
