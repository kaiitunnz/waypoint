"use client";

import { useEffect, useState } from "react";

import { fetchBackendModels, isAuthError } from "@/lib/api";
import { Backend, BackendModelOption } from "@/lib/types";

interface ModelPickerProps {
  host: string;
  token: string;
  backend: Backend;
  launchTargetId: string | null;
  value: string;
  onChange: (value: string) => void;
  onAuthFailure?: () => void;
  disabled?: boolean;
  label?: string;
  hint?: string;
}

export function ModelPicker({
  host,
  token,
  backend,
  launchTargetId,
  value,
  onChange,
  onAuthFailure,
  disabled,
  label = "Model",
  hint,
}: ModelPickerProps) {
  const [options, setOptions] = useState<BackendModelOption[]>([]);

  useEffect(() => {
    let cancelled = false;
    fetchBackendModels(host, token, backend, { launchTargetId })
      .then((response) => {
        if (cancelled) return;
        setOptions(response.models);
      })
      .catch((error) => {
        if (cancelled) return;
        if (isAuthError(error)) {
          onAuthFailure?.();
          return;
        }
        // Discovery failure leaves the picker as just "Default" — graceful
        // degradation instead of blocking the form.
        setOptions([]);
      });
    return () => {
      cancelled = true;
    };
  }, [host, token, backend, launchTargetId, onAuthFailure]);

  // Surface a custom-named model the caller already has even if it's not in
  // the curated list — covers schedules / sessions cloned from older state.
  const entries: BackendModelOption[] =
    value && !options.some((opt) => opt.id === value)
      ? [
          {
            id: value,
            label: `Custom · ${value}`,
            description: null,
          },
          ...options,
        ]
      : options;

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
          <option key={option.id} value={option.id}>
            {option.label}
          </option>
        ))}
      </select>
      {hint ? <span className="muted field-hint">{hint}</span> : null}
    </label>
  );
}
