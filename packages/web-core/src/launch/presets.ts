import { humaniseBackend, permissionModeLabel, type BackendCatalog } from "../backends";
import { effortLabel, modelLabelFor } from "../modelDisplay";
import type {
  AccountProfile,
  Backend,
  SessionPresetSpec,
  SessionPresetSpecSummary,
  SessionPresetSummary,
  UsageProviderOption,
} from "../types";

// The presets in list order with the default first.
export function orderPresets(presets: SessionPresetSummary[], defaultPresetId: string | null): SessionPresetSummary[] {
  return [...presets].sort((a, b) => {
    if (a.id === defaultPresetId) return -1;
    if (b.id === defaultPresetId) return 1;
    return 0;
  });
}

export function filterPresets(presets: SessionPresetSummary[], query: string): SessionPresetSummary[] {
  const needle = query.trim().toLowerCase();
  if (!needle) return presets;
  return presets.filter((preset) =>
    [preset.name, preset.description ?? "", preset.spec.backend ?? "", preset.spec.model ?? ""].some((field) =>
      field.toLowerCase().includes(needle),
    ),
  );
}

// "Claude Code, Opus 5.5, High, Auto": the agent, model, effort and permission
// mode a preset pins.
export function presetSubLabel(spec: SessionPresetSpecSummary, catalog?: BackendCatalog): string {
  const parts: string[] = [];
  const backend = spec.backend as Backend | null | undefined;
  if (backend) parts.push(humaniseBackend(backend, catalog));
  if (spec.model) parts.push(modelLabelFor(spec.model, []));
  if (spec.effort) parts.push(effortLabel(spec.effort));
  if (backend && spec.permission_mode) {
    parts.push(permissionModeLabel(backend, spec.permission_mode, catalog) ?? spec.permission_mode);
  }
  return parts.join(", ");
}

export interface SpecChip {
  text: string;
  // An account profile, shown in the account's hue.
  profile?: boolean;
  // Names something no longer offered here.
  unavailable?: boolean;
  title?: string;
}

// What a spec pins, as short chips after the agent: model, effort, permission
// mode, account profile, usage source, and counts of variables and arguments.
export function specSummaryChips(
  spec: SessionPresetSpecSummary,
  profiles: AccountProfile[],
  usageProviders: UsageProviderOption[],
): SpecChip[] {
  const chips: SpecChip[] = [];
  if (spec.model) chips.push({ text: spec.model });
  if (spec.effort) chips.push({ text: spec.effort });
  if (spec.permission_mode) chips.push({ text: spec.permission_mode });
  if (spec.account_profile_id) {
    const matched = profiles.find((profile) => profile.id === spec.account_profile_id);
    chips.push(
      matched
        ? { text: matched.label, profile: true }
        : {
            text: spec.account_profile_id,
            profile: true,
            unavailable: true,
            title: "Account profile no longer available",
          },
    );
  }
  if (spec.usage_limit_source === "usage_provider") {
    const provider = usageProviders.find((option) => option.id === spec.usage_provider_id);
    const account = provider?.accounts.find((entry) => entry.account_key === spec.usage_provider_account_key);
    chips.push(
      provider && account
        ? { text: `${provider.label} — ${account.account_label}` }
        : {
            text: spec.usage_provider_id ?? "usage provider",
            unavailable: true,
            title: "Usage provider account no longer available",
          },
    );
  }
  const envCount = spec.launch_env_keys?.length ?? 0;
  if (envCount) chips.push({ text: `${envCount} env` });
  const argsCount = spec.args?.length ?? 0;
  if (argsCount) chips.push({ text: `${argsCount} arg${argsCount > 1 ? "s" : ""}` });
  return chips;
}

// The redacted shape of a full spec, for showing what a save would capture.
export function summarizeSpec(spec: SessionPresetSpec): SessionPresetSpecSummary {
  return {
    backend: spec.backend,
    transport: spec.transport,
    model: spec.model,
    effort: spec.effort,
    permission_mode: spec.permission_mode,
    account_profile_id: spec.account_profile_id,
    usage_limit_source: spec.usage_limit_source,
    usage_provider_id: spec.usage_provider_id,
    usage_provider_account_key: spec.usage_provider_account_key,
    launch_env_keys: Object.keys(spec.launch_env ?? {}),
    args: spec.args,
    config_overrides: spec.config_overrides,
  };
}

// A preset with variables needs its full spec, values included, before it can
// fill a form; the redacted summary carries everything else.
export function presetNeedsFullSpec(preset: SessionPresetSummary): boolean {
  return (preset.spec.launch_env_keys?.length ?? 0) > 0;
}

// The summary spec as a full one, for a preset without variables.
export function specFromSummary(summary: SessionPresetSpecSummary): SessionPresetSpec {
  const spec: SessionPresetSpecSummary = { ...summary };
  delete spec.launch_env_keys;
  return spec;
}

export function presetBackendUnsupported(preset: SessionPresetSummary | null, launchable: Backend[]): Backend | null {
  const backend = preset?.spec.backend as Backend | null | undefined;
  return backend && !launchable.includes(backend) ? backend : null;
}

export function deletePresetMessage(preset: SessionPresetSummary): string {
  return preset.is_default
    ? `Delete the default preset "${preset.name}"? There will be no default afterwards. This cannot be undone.`
    : `Delete the preset "${preset.name}"? This cannot be undone.`;
}
