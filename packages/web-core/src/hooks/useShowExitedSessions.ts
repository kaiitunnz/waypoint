"use client";

import { useCallback, useSyncExternalStore } from "react";

import {
  LIST_SHOW_EXITED_KEY,
  PANEL_SHOW_EXITED_KEY,
  SWITCHER_SHOW_EXITED_KEY,
  readShowExited,
  writeShowExited,
} from "../showExited";

export { LIST_SHOW_EXITED_KEY, PANEL_SHOW_EXITED_KEY, SWITCHER_SHOW_EXITED_KEY };

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
