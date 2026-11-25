"use client";

import { useEffect, useState } from "react";

import { PANE_AUTO_QUERY, RAIL_AUTO_QUERY, sidePanelMode } from "@waypoint/web-core/layoutRules";

import { useMultiColumn, usePointerUI } from "@/lib/layout";
import { usePanelOpen } from "@/lib/local-setting";

import { panelToggle, type PanelControl, type SidePanel } from "./PanelButton";

// The toggle to focus on the next session screen: a session picked in a drawer
// replaces the screen the drawer was opened from.
let refocus: SidePanel | null = null;

// The session rail and side pane. As columns they stay as the person left them;
// as drawers they are open only until closed.
export function useSessionPanels() {
  const pointer = usePointerUI();
  const multiColumn = useMultiColumn();
  const mode = sidePanelMode(pointer, multiColumn);
  const railColumn = usePanelOpen("waypoint-session-rail", RAIL_AUTO_QUERY);
  const paneColumn = usePanelOpen("waypoint-session-pane", PANE_AUTO_QUERY);
  const [drawer, setDrawer] = useState<SidePanel | null>(null);
  // A window widened past the drawers must not bring one back when narrowed again.
  if (drawer !== null && mode !== "drawers") setDrawer(null);

  useEffect(() => {
    if (refocus === null) return;
    document.querySelector<HTMLElement>(panelToggle(refocus))?.focus();
    refocus = null;
  }, []);

  const control = (panel: SidePanel, [columnOpen, setColumnOpen]: ReturnType<typeof usePanelOpen>): PanelControl =>
    mode === "columns"
      ? { open: columnOpen, toggle: () => setColumnOpen(!columnOpen) }
      : { open: drawer === panel, toggle: () => setDrawer(drawer === panel ? null : panel) };

  return {
    mode,
    rail: control("rail", railColumn),
    pane: control("pane", paneColumn),
    drawer,
    closeDrawer: () => setDrawer(null),
    pickFromDrawer: (anotherSession: boolean) => {
      if (anotherSession) refocus = drawer;
      setDrawer(null);
    },
  };
}
