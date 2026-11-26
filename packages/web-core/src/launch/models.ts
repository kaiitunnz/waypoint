import type { BackendModelOption } from "../types";

// The choices for a model picker: a value not in the list, such as one carried
// over from older settings, stays choosable.
export function modelChoices(options: BackendModelOption[], value: string): BackendModelOption[] {
  return value && !options.some((option) => option.id === value)
    ? [{ id: value, label: `Custom · ${value}`, description: null }, ...options]
    : options;
}

// "Default (Opus 5.5)", or "Default" when the agent names no default.
export function defaultModelLabel(label: string | null | undefined): string {
  return label ? `Default (${label})` : "Default";
}
