"use client";

import { useCallback, useState } from "react";

import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";

function read<T extends string>(key: string, allowed: readonly T[]): T | null {
  try {
    const stored = localStorage.getItem(key);
    return allowed.find((value) => value === stored) ?? null;
  } catch {
    return null;
  }
}

// A choice remembered in this browser. Storage can be missing or throw (a private
// window), so the choice then lasts for the page. Read it only where the
// component renders on the client.
export function useLocalSetting<T extends string>(
  key: string,
  allowed: readonly T[],
  fallback: T,
): [T, (value: T) => void] {
  const [value, setValue] = useState<T>(() => read(key, allowed) ?? fallback);
  const set = useCallback(
    (next: T) => {
      setValue(next);
      try {
        localStorage.setItem(key, next);
      } catch {
        // Not remembered.
      }
    },
    [key],
  );
  return [value, set];
}

// Whether a panel is open: it follows the media `query` until the person opens or closes it.
export function usePanelOpen(key: string, query: string): [boolean, (open: boolean) => void] {
  const fits = useMediaQuery(query);
  const [stored, set] = useLocalSetting(key, ["open", "closed", "auto"] as const, "auto");
  const setOpen = useCallback((open: boolean) => set(open ? "open" : "closed"), [set]);
  return [stored === "auto" ? fits : stored === "open", setOpen];
}
