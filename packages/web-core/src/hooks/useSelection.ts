"use client";

import { useCallback, useEffect, useState } from "react";

import { pruneSelection, selectAllState, toggleAll, toggleId } from "../inbox";

// Select mode over the listed ids. Pass the ids shown, so nothing hidden is ever
// selected, and keep the array stable between renders.
export function useSelection(listed: readonly string[]) {
  const [selecting, setSelecting] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(() => new Set());

  // Ids no longer listed (deleted elsewhere, paged out) drop out.
  useEffect(() => {
    setSelected((current) => pruneSelection(current, listed));
  }, [listed]);

  const start = useCallback(() => setSelecting(true), []);
  const exit = useCallback(() => {
    setSelecting(false);
    setSelected(new Set());
  }, []);
  const toggle = useCallback((id: string) => setSelected((current) => toggleId(current, id)), []);
  const toggleEvery = useCallback(() => setSelected((current) => toggleAll(current, listed)), [listed]);

  return {
    selecting,
    selected,
    allState: selectAllState(selected, listed),
    start,
    exit,
    toggle,
    toggleAll: toggleEvery,
  };
}
