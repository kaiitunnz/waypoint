"use client";

import { effortLabel } from "@/lib/modelDisplay";

interface EffortPickerProps {
  options: string[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
  hint?: string;
}

export function EffortPicker({
  options,
  value,
  onChange,
  disabled,
  label = "Reasoning effort",
  hint,
}: EffortPickerProps) {
  if (options.length === 0) {
    return null;
  }
  // Surface the current value if the picker no longer lists it (e.g. the user
  // switched models and the previous level isn't supported anymore). Keeping
  // it visible avoids a silent "Default" snap until the user re-picks.
  const entries = value && !options.includes(value) ? [value, ...options] : options;
  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">Default</option>
        {entries.map((option) => (
          <option key={option} value={option}>
            {effortLabel(option)}
          </option>
        ))}
      </select>
      {hint ? <span className="muted field-hint">{hint}</span> : null}
    </label>
  );
}
