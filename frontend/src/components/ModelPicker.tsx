"use client";

import { useEffect } from "react";

import { useLaunchModels } from "@waypoint/web-core/hooks/useLaunchModels";
import {
  defaultModelLabel as defaultModelChoiceLabel,
  modelChoices,
} from "@waypoint/web-core/launch/models";
import { Backend, BackendModelListResponse } from "@waypoint/web-core/types";

interface ModelPickerProps {
  host: string;
  token: string;
  backend: Backend;
  launchTargetId: string | null;
  accountProfileId?: string | null;
  value: string;
  onChange: (value: string) => void;
  onAuthFailure?: () => void;
  // Fires after each successful discovery response so callers (e.g. an
  // EffortPicker rendered alongside) can derive their own options without
  // duplicating the fetch — Codex discovery spawns a transient client per
  // call.
  onModelsLoaded?: (response: BackendModelListResponse) => void;
  disabled?: boolean;
  label?: string;
  hint?: string;
  defaultModelLabel?: string | null;
}

export function ModelPicker({
  host,
  token,
  backend,
  launchTargetId,
  accountProfileId,
  value,
  onChange,
  onAuthFailure,
  onModelsLoaded,
  disabled,
  label = "Model",
  hint,
  defaultModelLabel,
}: ModelPickerProps) {
  const response = useLaunchModels(
    host,
    token,
    backend,
    launchTargetId,
    accountProfileId ?? null,
    onAuthFailure,
  );

  useEffect(() => {
    if (response) onModelsLoaded?.(response);
  }, [response, onModelsLoaded]);

  const entries = modelChoices(response?.models ?? [], value);

  return (
    <label className="field">
      <span>{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        disabled={disabled}
      >
        <option value="">{defaultModelChoiceLabel(defaultModelLabel)}</option>
        {entries.map((option) => (
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <span className="muted field-hint">{hint}</span> : null}
    </label>
  );
}
