import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import { launchCatalogDescriptors } from "../test-helpers";
import type { SessionPresetSummary } from "../types";
import {
  deletePresetMessage,
  filterPresets,
  loadPresetSpec,
  openingPreset,
  orderPresets,
  presetBackendUnsupported,
  presetNeedsFullSpec,
  presetSubLabel,
  specFromSummary,
  specSummaryChips,
  summarizeSpec,
} from "./presets";

const preset = (id: string, spec: SessionPresetSummary["spec"], extra: Partial<SessionPresetSummary> = {}) =>
  ({ id, name: id, spec, is_default: false, created_at: "", updated_at: "", ...extra }) as SessionPresetSummary;

const catalog = buildCatalog(launchCatalogDescriptors());

describe("presets", () => {
  const list = [
    preset("codex-worker", { backend: "codex", model: "gpt-6" }),
    preset("claude-main", { backend: "claude_code", model: "claude-opus-5-5", effort: "high", permission_mode: "auto" }, { is_default: true }),
  ];

  it("lists the default first and filters by name, agent or model", () => {
    expect(orderPresets(list, "claude-main").map((p) => p.id)).toEqual(["claude-main", "codex-worker"]);
    expect(orderPresets(list, null).map((p) => p.id)).toEqual(["codex-worker", "claude-main"]);
    expect(filterPresets(list, "GPT").map((p) => p.id)).toEqual(["codex-worker"]);
    expect(filterPresets(list, " ")).toBe(list);
  });

  it("describes a preset by agent, model, effort and mode", () => {
    expect(presetSubLabel(list[1].spec, catalog)).toBe("Claude Code, Opus 5.5, High, Auto");
    expect(presetSubLabel({}, catalog)).toBe("");
  });

  it("summarises what a spec captures", () => {
    const spec = {
      backend: "claude_code",
      model: "opus",
      account_profile_id: "gone",
      usage_limit_source: "usage_provider" as const,
      usage_provider_id: "lumid",
      usage_provider_account_key: "me",
      launch_env: { A: "1", B: "2" },
      args: ["--x"],
    };
    const chips = specSummaryChips(summarizeSpec(spec), [], [
      { id: "lumid", label: "Lumid", accounts: [{ account_key: "me", account_label: "Me" }] } as never,
    ]);
    expect(chips.map((chip) => chip.text)).toEqual(["opus", "gone", "Lumid — Me", "2 env", "1 arg"]);
    expect(chips[1]).toMatchObject({ profile: true, unavailable: true });
  });

  it("needs the full spec only for presets with variables", () => {
    expect(presetNeedsFullSpec(preset("a", { launch_env_keys: ["K"] }))).toBe(true);
    expect(presetNeedsFullSpec(preset("b", {}))).toBe(false);
    expect(specFromSummary({ model: "m", launch_env_keys: [] })).toEqual({ model: "m" });
  });

  it("flags a preset whose agent cannot launch here", () => {
    expect(presetBackendUnsupported(list[0], ["claude_code"])).toBe("codex");
    expect(presetBackendUnsupported(list[0], ["codex"])).toBeNull();
    expect(presetBackendUnsupported(null, [])).toBeNull();
  });

  it("warns that deleting the default leaves none", () => {
    expect(deletePresetMessage(list[1])).toContain("no default afterwards");
    expect(deletePresetMessage(list[0])).toBe('Delete the preset "codex-worker"? This cannot be undone.');
  });
});

describe("opening a preset", () => {
  const plain = preset("plain", { backend: "codex", model: "gpt" });
  const withEnv = preset("env", { backend: "codex", launch_env_keys: ["A"] });

  it("opens the default or the chosen preset, with its spec when no values are needed", () => {
    expect(openingPreset([plain, withEnv], "plain", { kind: "default" })).toEqual({
      id: "plain",
      spec: { backend: "codex", model: "gpt" },
    });
    expect(openingPreset([plain, withEnv], "plain", { kind: "preset", presetId: "env" })).toEqual({ id: "env", spec: null });
    expect(openingPreset([plain], "plain", { kind: "preset", presetId: null })).toEqual({ id: null, spec: null });
    expect(openingPreset([plain], "gone", { kind: "default" })).toEqual({ id: null, spec: null });
  });

  it("fetches the full spec only for a preset with variables", async () => {
    const fetched = { ...withEnv, spec: { backend: "codex", launch_env: { A: "1" } } };
    const fetchPreset = async (id: string) => {
      if (id !== "env") throw new Error(id);
      return fetched as never;
    };
    expect(await loadPresetSpec(plain, fetchPreset)).toEqual({ backend: "codex", model: "gpt" });
    expect(await loadPresetSpec(withEnv, fetchPreset)).toEqual({ backend: "codex", launch_env: { A: "1" } });
  });
});
