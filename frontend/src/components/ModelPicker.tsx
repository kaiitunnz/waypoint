"use client";

import { defaultModelChoiceLabel, modelChoices } from "@waypoint/web-core/launch/models";
import type { BackendModelOption } from "@waypoint/web-core/types";

interface ModelPickerProps {
  models: BackendModelOption[];
  defaultModelLabel: string | null;
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export function ModelPicker({ models, defaultModelLabel, value, onChange, disabled }: ModelPickerProps) {
  return (
    <label className="field">
      <span>Model</span>
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled}>
        <option value="">{defaultModelChoiceLabel(defaultModelLabel)}</option>
        {modelChoices(models, value).map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}
