"use client";

import { useMemo, useSyncExternalStore } from "react";

import { storedFlag } from "../storedFlag";

// A remembered on/off choice, `fallback` until one is made; see `storedFlag`.
export function useStoredFlag(key: string, fallback: boolean): [boolean, (value: boolean) => void] {
  const flag = useMemo(() => storedFlag(key, fallback), [key, fallback]);
  const value = useSyncExternalStore(flag.subscribe, flag.get, () => fallback);
  return [value, flag.set];
}
