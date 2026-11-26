"use client";

import { useConversationPreviews } from "@waypoint/web-core/hooks/useConversationPreviews";

import { PickerRow, SettingsSection, SwitchRow } from "@/components/ui/Settings";
import { useTheme } from "@/lib/theme";

export function ThemeRow() {
  const { theme, setTheme } = useTheme();
  return (
    <PickerRow
      label="Theme"
      value={theme}
      onChange={setTheme}
      options={[
        { value: "dark", label: "Dark" },
        { value: "light", label: "Light" },
      ]}
    />
  );
}

export function Appearance() {
  return (
    <SettingsSection>
      <ThemeRow />
    </SettingsSection>
  );
}

export function SessionLists() {
  const [previews, setPreviews] = useConversationPreviews();
  return (
    <SettingsSection>
      <SwitchRow label="Conversation previews" checked={previews} onChange={setPreviews} />
    </SettingsSection>
  );
}
