import type { BackendModelOption } from "../types";

// The choices for a model picker: a value not in the list, such as one carried
// over from older settings, stays choosable.
export function modelChoices(options: readonly BackendModelOption[], value: string | null): BackendModelOption[] {
  return value && !options.some((option) => option.id === value)
    ? [{ id: value, label: `Custom · ${value}`, description: null }, ...options]
    : [...options];
}

// The effort choices, led by the current level when the options lack it.
export function effortChoices(options: readonly string[], value: string): string[] {
  return value && !options.includes(value) ? [value, ...options] : [...options];
}

// "Default (Opus 5.5)", or "Default" when the agent names no default.
export function defaultModelChoiceLabel(label: string | null | undefined): string {
  return label ? `Default (${label})` : "Default";
}

// The effort levels for the chosen model, or for the agent's default model. A
// model with unknown levels offers the agent's whole vocabulary; with no model to
// go by, the union of every model's levels.
export function effortLevelsFor(
  models: readonly BackendModelOption[],
  effortLevels: readonly string[],
  model: string | null,
  defaultModelId: string | null,
): string[] {
  const resolved = model || defaultModelId;
  const entry = resolved ? models.find((option) => option.id === resolved) : undefined;
  if (entry) return [...(entry.supported_efforts ?? effortLevels)];
  return [...new Set(models.flatMap((option) => option.supported_efforts ?? []))];
}
