"use client";

import { createContext, useContext } from "react";

export interface NeedsYouContextValue {
  // Queue and item side by side.
  columns: boolean;
  selected: string | null;
  leave: () => void;
}

export const NeedsYouContext = createContext<NeedsYouContextValue | null>(null);

export function useNeedsYou(): NeedsYouContextValue {
  const value = useContext(NeedsYouContext);
  if (!value) throw new Error("useNeedsYou needs a NeedsYouScreen");
  return value;
}
