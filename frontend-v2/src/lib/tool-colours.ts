"use client";

import { useStoredFlag } from "@waypoint/web-core/hooks/useStoredFlag";
import { useEffect } from "react";

const ON_FOCUS_KEY = "waypoint.tool-colours-on-focus";

// Whether a tool glyph is coloured only while its holder is hovered, focused or open.
export function useToolColoursOnFocus(): [boolean, (next: boolean) => void] {
  return useStoredFlag(ON_FOCUS_KEY, false);
}

// Mirrors the choice onto <html>, where ToolGlyph.module.css reads it.
export function useToolColoursAttribute() {
  const [onFocus] = useToolColoursOnFocus();
  useEffect(() => {
    if (onFocus) document.documentElement.dataset.toolColours = "focus";
    else delete document.documentElement.dataset.toolColours;
  }, [onFocus]);
}
