import { describe, expect, it } from "vitest";

import type { SessionRecord } from "./types";
import {
  PLUGIN_SELECTION,
  sameSelection,
  selectionFromRecord,
  selectUsageSource,
  usageSourceChoices,
} from "./usageLimitSource";

const record = (fields: Record<string, unknown>) => fields as unknown as SessionRecord;

describe("selectionFromRecord", () => {
  it("returns the plugin selection unless the record uses a usage provider", () => {
    expect(selectionFromRecord(record({}))).toBe(PLUGIN_SELECTION);
    expect(selectionFromRecord(record({ usage_limit_source: "plugin" }))).toBe(PLUGIN_SELECTION);
    expect(selectionFromRecord(record({ usage_limit_source: "plugin", usage_provider_id: "p" }))).toBe(
      PLUGIN_SELECTION,
    );
  });

  it("carries the provider and account of a usage-provider record", () => {
    expect(
      selectionFromRecord(
        record({
          usage_limit_source: "usage_provider",
          usage_provider_id: "lumid",
          usage_provider_account_key: "acct",
        }),
      ),
    ).toEqual({ source: "usage_provider", providerId: "lumid", accountKey: "acct" });
  });

  it("defaults missing provider fields to null", () => {
    expect(selectionFromRecord(record({ usage_limit_source: "usage_provider" }))).toEqual({
      source: "usage_provider",
      providerId: null,
      accountKey: null,
    });
  });
});

describe("sameSelection", () => {
  const sel = (source: "plugin" | "usage_provider", providerId: string | null, accountKey: string | null) => ({
    source,
    providerId,
    accountKey,
  });

  it("compares source, provider and account", () => {
    expect(sameSelection(PLUGIN_SELECTION, sel("plugin", null, null))).toBe(true);
    expect(sameSelection(sel("usage_provider", "p", "a"), sel("usage_provider", "p", "a"))).toBe(true);
    expect(sameSelection(sel("usage_provider", "p", "a"), sel("usage_provider", "p", "b"))).toBe(false);
    expect(sameSelection(sel("usage_provider", "p", "a"), sel("usage_provider", "q", "a"))).toBe(false);
    expect(sameSelection(PLUGIN_SELECTION, sel("usage_provider", null, null))).toBe(false);
  });
});

describe("usage source choices", () => {
  const options = [
    { id: "lumid", label: "Lumid", accounts: [{ account_key: "a", account_label: "Team" }, { account_key: "b", account_label: "Me" }] },
    { id: "solo", label: "Solo", accounts: [{ account_key: "s", account_label: "Only" }] },
    { id: "empty", label: "Empty", accounts: [] },
  ] as never[];

  it("names single accounts, disables empty providers and offers multi-account picks", () => {
    const plugin = usageSourceChoices(options, PLUGIN_SELECTION);
    expect(plugin.value).toBe("plugin");
    expect(plugin.choices.map((choice) => [choice.label, Boolean(choice.disabled)])).toEqual([
      ["Agent plugin", false],
      ["Lumid", false],
      ["Solo — Only", false],
      ["Empty (unavailable)", true],
    ]);
    const lumid = usageSourceChoices(options, { source: "usage_provider", providerId: "lumid", accountKey: "b" });
    expect(lumid.value).toBe("provider:lumid");
    expect(lumid.accounts).toHaveLength(2);
  });

  it("keeps a saved choice that is no longer published, disabled", () => {
    const gone = usageSourceChoices(options, { source: "usage_provider", providerId: "old", accountKey: "x" });
    expect(gone.value).toBe("__unavailable__");
    expect(gone.choices.at(-1)).toEqual({ value: "__unavailable__", label: "Unavailable: old account", disabled: true });
  });

  it("selects a provider on its first account", () => {
    expect(selectUsageSource(options, "plugin")).toBe(PLUGIN_SELECTION);
    expect(selectUsageSource(options, "provider:lumid")).toEqual({ source: "usage_provider", providerId: "lumid", accountKey: "a" });
    expect(selectUsageSource(options, "provider:empty")).toBeNull();
    expect(selectUsageSource(options, "__unavailable__")).toBeNull();
  });
});
