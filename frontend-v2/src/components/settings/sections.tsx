"use client";

import type { ComponentType } from "react";

import { useConversationPreviews } from "@waypoint/web-core/hooks/useConversationPreviews";

import { PickerRow, SettingsSection, SwitchRow } from "@/components/ui/Settings";
import { useTheme } from "@/lib/theme";

// Device sections are this browser's own choices; server sections belong to the backend
// in use and read it from the shell. A section renders one or more SettingsSections.
export type SettingsScope = "device" | "server";

export interface SettingsSectionSpec {
  // The section's anchor and its path under /settings.
  id: string;
  title: string;
  scope: SettingsScope;
  Component: ComponentType;
}

function Appearance() {
  const { theme, setTheme } = useTheme();
  return (
    <SettingsSection>
      <PickerRow
        label="Theme"
        value={theme}
        onChange={setTheme}
        options={[
          { value: "dark", label: "Dark" },
          { value: "light", label: "Light" },
        ]}
      />
    </SettingsSection>
  );
}

function SessionLists() {
  const [previews, setPreviews] = useConversationPreviews();
  return (
    <SettingsSection>
      <SwitchRow label="Conversation previews" checked={previews} onChange={setPreviews} />
    </SettingsSection>
  );
}

// In page order. A new section is one entry here.
export const SETTINGS_SECTIONS: SettingsSectionSpec[] = [
  { id: "appearance", title: "Appearance", scope: "device", Component: Appearance },
  { id: "session-lists", title: "Session lists", scope: "device", Component: SessionLists },
];
