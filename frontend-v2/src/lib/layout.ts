"use client";

import { useCallback, useState, type RefCallback } from "react";

import { useMediaQuery } from "@waypoint/web-core/hooks/use-media-query";
import { isPhoneLayout, MULTI_COLUMN_QUERY, POINTER_QUERY } from "@waypoint/web-core/layoutRules";

// Callers render below AuthGate, in the browser only, so these are right from
// their first render; the stylesheets settle the first paint.

export function usePointerUI(): boolean {
  return useMediaQuery(POINTER_QUERY);
}

export function useMultiColumn(): boolean {
  return useMediaQuery(MULTI_COLUMN_QUERY);
}

export function usePhoneLayout(): boolean {
  const pointer = usePointerUI();
  const multiColumn = useMultiColumn();
  return isPhoneLayout(pointer, multiColumn);
}

// Whether the box of the element holding the ref is narrower than `width`.
// Measured before the first paint.
export function useNarrowerThan(width: number): [RefCallback<HTMLElement>, boolean] {
  const [narrower, setNarrower] = useState(false);
  const ref = useCallback(
    (node: HTMLElement | null) => {
      if (!node) return;
      const measure = () => setNarrower(node.clientWidth < width);
      measure();
      const observer = new ResizeObserver(measure);
      observer.observe(node);
      return () => observer.disconnect();
    },
    [width],
  );
  return [ref, narrower];
}
