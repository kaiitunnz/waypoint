import { describe, expect, it } from "vitest";

import type { SessionRecord } from "./types";
import { PLUGIN_SELECTION, sameSelection, selectionFromRecord } from "./usageLimitSource";

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
