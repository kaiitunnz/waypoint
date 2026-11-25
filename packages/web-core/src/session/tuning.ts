/**
 * Permission mode, model and effort as the composer offers them.
 */

import { type BackendCatalog } from "../backends";
import { effortLabel } from "../modelDisplay";
import type { BackendModelOption, BackendPermissionMode, SessionRecord } from "../types";

export type TuningField = "mode" | "model" | "effort";

export interface TuningInput {
  modeOptions: readonly BackendPermissionMode[];
  modelOptions: readonly BackendModelOption[];
  effortLevels: readonly string[];
  defaultModelId: string | null;
  defaultModelLabel: string | null;
  currentModel: string | null;
  currentEffort: string | null;
  permissionMode: string | null;
}

export interface TuningOptions {
  // The model choices, led by the session's own model when the list lacks it.
  modelEntries: BackendModelOption[];
  effortOptions: string[];
  hasModelPicker: boolean;
  hasEffortPicker: boolean;
}

export function tuningOptions(input: TuningInput): TuningOptions {
  const { modelOptions, effortLevels, currentModel, currentEffort, defaultModelId } = input;
  // A custom-named model the session already has is listed, so the picker
  // shows it instead of "Default".
  const modelEntries =
    currentModel && !modelOptions.some((option) => option.id === currentModel)
      ? [{ id: currentModel, label: `Custom · ${currentModel}`, description: null }, ...modelOptions]
      : [...modelOptions];
  // Efforts are gated by the selected model; without one, by the default model.
  const resolved = currentModel || defaultModelId;
  const entry = resolved ? modelOptions.find((option) => option.id === resolved) : undefined;
  const effortOptions = entry
    ? [...(entry.supported_efforts ?? effortLevels)] // null → unknown: the agent's whole vocabulary
    : Array.from(new Set(modelOptions.flatMap((option) => option.supported_efforts ?? [])));
  return {
    modelEntries,
    effortOptions,
    hasModelPicker: modelOptions.length > 0 || currentModel !== null,
    hasEffortPicker: effortOptions.length > 0 || currentEffort !== null,
  };
}

// The words of the control that opens the tuning: mode, model and effort.
export function tuningSummary(input: TuningInput, options: TuningOptions): string[] {
  const parts: string[] = [];
  if (input.modeOptions.length > 0) {
    const matched = input.modeOptions.find((option) => option.id === (input.permissionMode ?? "default"));
    parts.push(matched?.label ?? "Default");
  }
  if (options.hasModelPicker) {
    const matched = options.modelEntries.find((option) => option.id === (input.currentModel ?? ""));
    parts.push(
      matched?.label ??
        (input.currentModel || (input.defaultModelLabel ? `Default (${input.defaultModelLabel})` : "Default")),
    );
  }
  if (options.hasEffortPicker) {
    parts.push(input.currentEffort ? effortLabel(input.currentEffort) : "Default");
  }
  return parts;
}

// The mode after the shown one, wrapping.
export function nextModeId(modeOptions: readonly BackendPermissionMode[], shown: string): string | null {
  if (modeOptions.length === 0) return null;
  const index = modeOptions.findIndex((option) => option.id === shown);
  return modeOptions[(index + 1) % modeOptions.length].id;
}

export type RestartFields = Record<TuningField, boolean>;

// Which changes the session applies by restarting: those are staged and applied
// together. Mode and model resolve against the (agent, transport) pair; effort
// is the agent descriptor's own flag.
export function restartFields(session: SessionRecord | null, catalog: BackendCatalog): RestartFields {
  const pair = session?.transport ? catalog.capsFor(session.backend, session.transport) : undefined;
  return {
    mode: Boolean(pair?.supports_set_permission_mode_with_restart),
    model: Boolean(pair?.supports_set_model_with_restart),
    effort: Boolean(session && catalog.byId(session.backend)?.capabilities.supports_set_effort_with_restart),
  };
}

// Whether changing a setting now would cut off the running turn.
export function changeInterruptsTurn(session: SessionRecord, catalog: BackendCatalog): boolean {
  const interrupts = catalog.byId(session.backend)?.capabilities.settings_change_interrupts_turn;
  return Boolean(interrupts) && (session.status === "running" || session.status === "waiting_input");
}

// Picks waiting for a restart; null where nothing is picked.
export interface StagedPicks {
  mode: string | null;
  model: string | null;
  effort: string | null;
}

export const NO_STAGED_PICKS: StagedPicks = { mode: null, model: null, effort: null };

export interface TuningDraft {
  // What each control shows: the staged pick, or the session's own value.
  shown: { mode: string; model: string; effort: string };
  // The staged picks that differ from the session's values.
  pending: StagedPicks;
  hasPending: boolean;
}

export function tuningDraft(input: TuningInput, staged: StagedPicks, restart: RestartFields): TuningDraft {
  const current = {
    mode: input.permissionMode ?? "default",
    model: input.currentModel ?? "",
    effort: input.currentEffort ?? "",
  };
  const shown = { ...current };
  const pending: StagedPicks = { ...NO_STAGED_PICKS };
  for (const field of ["mode", "model", "effort"] as const) {
    // A staged pick is ignored once the transport applies inline (after an
    // interface switch).
    const pick = restart[field] ? staged[field] : null;
    if (pick === null) continue;
    shown[field] = pick;
    if (pick !== current[field]) pending[field] = pick;
  }
  return { shown, pending, hasPending: Object.values(pending).some((value) => value !== null) };
}

// "Plan mode and Opus 5.5": the staged changes, for the restart prompt.
export function pendingRestartLabel(
  draft: TuningDraft,
  modeOptions: readonly BackendPermissionMode[],
  modelEntries: readonly BackendModelOption[],
): string {
  const { mode, model, effort } = draft.pending;
  return [
    mode !== null && mode !== ""
      ? `${modeOptions.find((option) => option.id === mode)?.label ?? mode} mode`
      : null,
    model !== null
      ? model
        ? (modelEntries.find((option) => option.id === model)?.label ?? model)
        : "the default model"
      : null,
    effort !== null ? (effort ? `${effortLabel(effort)} effort` : "the default effort") : null,
  ]
    .filter(Boolean)
    .join(" and ");
}
