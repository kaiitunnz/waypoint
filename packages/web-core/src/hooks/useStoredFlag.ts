"use client";

import { useMemo, useSyncExternalStore } from "react";

import { storedFlag } from "../storedFlag";

// `storage` must be stable, such as `windowStorage`.
export function useStoredFlag(
  key: string,
  fallback: boolean,
  storage?: () => Storage | null,
): [boolean, (value: boolean) => void] {
  const flag = useMemo(() => storedFlag(key, fallback, storage), [key, fallback, storage]);
  const value = useSyncExternalStore(flag.subscribe, flag.get, () => fallback);
  return [value, flag.set];
}
