import type { SessionRecord, UsageLimitSource, UsageProviderAccountOption, UsageProviderOption } from "./types";

export interface UsageLimitSourceValue {
  source: UsageLimitSource;
  providerId: string | null;
  accountKey: string | null;
}

export const PLUGIN_SELECTION: UsageLimitSourceValue = {
  source: "plugin",
  providerId: null,
  accountKey: null,
};

export function selectionFromRecord(record: SessionRecord): UsageLimitSourceValue {
  if (record.usage_limit_source === "usage_provider") {
    return {
      source: "usage_provider",
      providerId: record.usage_provider_id ?? null,
      accountKey: record.usage_provider_account_key ?? null,
    };
  }
  return PLUGIN_SELECTION;
}

export function sameSelection(
  a: UsageLimitSourceValue,
  b: UsageLimitSourceValue,
): boolean {
  return (
    a.source === b.source &&
    a.providerId === b.providerId &&
    a.accountKey === b.accountKey
  );
}

const USAGE_PLUGIN_LABEL = "Agent plugin";
const UNAVAILABLE_CHOICE = "__unavailable__";

export interface UsageSourceChoice {
  value: string;
  label: string;
  disabled?: boolean;
}

export interface UsageSourceChoices {
  value: string;
  choices: UsageSourceChoice[];
  // The selected provider's accounts, when it has more than one to choose from.
  accounts: UsageProviderAccountOption[];
}

// The "usage limits from" choices: the agent plugin, then each provider. A
// provider with one account names it; one with none cannot be picked. A saved
// provider or account no longer published stays shown, disabled, until replaced.
export function usageSourceChoices(options: UsageProviderOption[], value: UsageLimitSourceValue): UsageSourceChoices {
  const provider =
    value.source === "usage_provider" && value.providerId
      ? (options.find((option) => option.id === value.providerId) ?? null)
      : null;
  const accountExists = provider?.accounts.some((account) => account.account_key === value.accountKey) ?? false;
  const unavailable = value.source === "usage_provider" && !accountExists;
  const choices: UsageSourceChoice[] = [
    { value: "plugin", label: USAGE_PLUGIN_LABEL },
    ...options.map((option) => ({
      value: `provider:${option.id}`,
      label:
        option.accounts.length === 0
          ? `${option.label} (unavailable)`
          : option.accounts.length === 1
            ? `${option.label} — ${option.accounts[0].account_label}`
            : option.label,
      disabled: option.accounts.length === 0,
    })),
  ];
  if (unavailable) {
    choices.push({
      value: UNAVAILABLE_CHOICE,
      label: value.providerId ? `Unavailable: ${value.providerId} account` : "Unavailable account",
      disabled: true,
    });
  }
  return {
    value: unavailable ? UNAVAILABLE_CHOICE : provider ? `provider:${provider.id}` : "plugin",
    choices,
    accounts: provider && provider.accounts.length > 1 ? provider.accounts : [],
  };
}

// The selection for a picked choice, or null when it cannot be picked. A
// provider starts on its first account.
export function selectUsageSource(options: UsageProviderOption[], choice: string): UsageLimitSourceValue | null {
  if (choice === "plugin") return PLUGIN_SELECTION;
  if (!choice.startsWith("provider:")) return null;
  const id = choice.slice("provider:".length);
  const provider = options.find((option) => option.id === id);
  if (!provider || provider.accounts.length === 0) return null;
  return { source: "usage_provider", providerId: id, accountKey: provider.accounts[0].account_key };
}
