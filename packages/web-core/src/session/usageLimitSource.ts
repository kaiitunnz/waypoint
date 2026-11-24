import type { SessionRecord, UsageLimitSource } from "../types";

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
