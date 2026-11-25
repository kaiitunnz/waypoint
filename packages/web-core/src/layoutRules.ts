// Input picks the chrome and width picks the layout. Stylesheets repeat these
// queries: the input one for chrome and density, the width one for layout.
export const POINTER_QUERY = "(hover: hover) and (pointer: fine)";
export const MULTI_COLUMN_QUERY = "(min-width: 900px)";

// From these widths the session rail and side pane open until the person decides.
export const RAIL_AUTO_QUERY = "(min-width: 1100px)";
export const PANE_AUTO_QUERY = "(min-width: 1280px)";

// A pointer session header narrower than this takes its compact controls.
export const COMPACT_HEADER_WIDTH = 580;

// How the session rail and side pane appear: beside the conversation, drawn over
// it on request, or not at all.
export type SidePanelMode = "columns" | "drawers" | "none";

export function sidePanelMode(pointer: boolean, multiColumn: boolean): SidePanelMode {
  if (multiColumn) return "columns";
  return pointer ? "drawers" : "none";
}

// A touch screen with room for one column gets the phone composition of a page;
// every other client gets the full one, reflowed to its width.
export function isPhoneLayout(pointer: boolean, multiColumn: boolean): boolean {
  return !pointer && !multiColumn;
}
