"use client";

import { useCallback, useState } from "react";

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

// A panel's open state the person may override: unset follows `auto`, set wins.
export function useOverride(key: string): [boolean | null, (open: boolean) => void] {
  const [stored, set] = useLocalSetting(key, ["open", "closed", "auto"] as const, "auto");
  return [stored === "auto" ? null : stored === "open", (open) => set(open ? "open" : "closed")];
}
