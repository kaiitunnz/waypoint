import type { ComponentType } from "react";

import { Appearance, SessionLists } from "./DeviceSections";

// Device sections are this browser's own choices; server sections belong to the backend
// in use and read it from the shell. A section renders one or more SettingsSections.
// No "use client": the route reads the ids on the server.
export type SettingsScope = "device" | "server";

export interface SettingsSectionSpec {
  // The section's anchor and its path under /settings.
  id: string;
  title: string;
  scope: SettingsScope;
  Component: ComponentType;
}

// In page order. A new section is one entry here.
export const SETTINGS_SECTIONS: SettingsSectionSpec[] = [
  { id: "appearance", title: "Appearance", scope: "device", Component: Appearance },
  { id: "session-lists", title: "Session lists", scope: "device", Component: SessionLists },
];
