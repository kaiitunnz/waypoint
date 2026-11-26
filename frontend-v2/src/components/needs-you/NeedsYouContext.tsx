"use client";

import { createContext, useContext } from "react";

export interface NeedsYouContextValue {
  // Queue and item side by side, rather than one at a time.
  columns: boolean;
  // The route id of the open item.
  selected: string | null;
  // Back to the queue.
  leave: () => void;
}

export const NeedsYouContext = createContext<NeedsYouContextValue | null>(null);

export function useNeedsYou(): NeedsYouContextValue {
  const value = useContext(NeedsYouContext);
  if (!value) throw new Error("useNeedsYou needs a NeedsYouScreen");
  return value;
}
