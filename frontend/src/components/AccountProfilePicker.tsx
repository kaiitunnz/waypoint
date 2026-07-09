"use client";

import type { AccountProfile } from "@/lib/types";

interface AccountProfilePickerProps {
  profiles: AccountProfile[];
  value: string;
  onChange: (id: string) => void;
  disabled?: boolean;
  // Copy for the no-profile option. "Account" overpromises (the frontend only
  // knows the configured profile, not the verified account), and plain
  // "Default" collides with default preset/model/effort — so callers name the
  // fallback explicitly, e.g. "Service default".
  defaultLabel?: string;
  label?: string;
  // Wrapper class so the same control reads as a flat launch ``.field`` or a
  // composer ``.composer-tune-field`` depending on where it's mounted.
  fieldClassName?: string;
}

// The account/config-profile selector shared by the launch context block and
// the running-session switch flow. Presentational only: it renders the profile
// as session identity and leaves the destructive/restart semantics to callers.
export function AccountProfilePicker({
  profiles,
  value,
  onChange,
  disabled,
  defaultLabel = "Service default",
  label = "Account profile",
  fieldClassName = "field",
}: AccountProfilePickerProps) {
  // Keep a currently-selected id visible even if it has dropped out of the
  // catalogue (a historical profile), so the control never silently blanks.
  const missing = value !== "" && !profiles.some((profile) => profile.id === value);
  return (
    <label className={fieldClassName}>
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">{defaultLabel}</option>
        {profiles.map((profile) => (
          <option key={profile.id} value={profile.id}>
            {profile.label}
          </option>
        ))}
        {missing ? (
          <option value={value}>{value}</option>
        ) : null}
      </select>
    </label>
  );
}
