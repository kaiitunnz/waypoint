import { describe, expect, it } from "vitest";

import { buildCatalog } from "../backends";
import { launchCatalogDescriptors } from "../test-helpers";
import type { BackendModelListResponse } from "../types";
import {
  advancedOverrides,
  applyPresetSpec,
  changeAgent,
  clampLaunchForm,
  createSessionRequest,
  effortOptionsFor,
  initialLaunchForm,
  launchCapabilities,
  presetSpecFromForm,
  scheduleLaunchRequest,
  type LaunchFormContext,
} from "./form";

const catalog = buildCatalog(launchCatalogDescriptors());
const context: LaunchFormContext = {
  catalog,
  defaultLaunchEnvByBackend: {
    claude_code: { CLAUDE_HOME: "/c" },
    codex: { CODEX_HOME: "/x" },
    opencode: {},
  },
  accountProfilesByBackend: { claude_code: [{ id: "work", label: "Work" }] as never[] },
};

const models: BackendModelListResponse = {
  backend: "claude_code",
  models: [
    { id: "opus", label: "Opus", supported_efforts: ["low", "high", "xhigh"] },
    { id: "haiku", label: "Haiku", supported_efforts: [] },
    { id: "sonnet", label: "Sonnet", supported_efforts: null },
  ],
  default_model_id: "opus",
  effort_levels: ["low", "medium", "high"],
} as unknown as BackendModelListResponse;

describe("initialLaunchForm", () => {
  it("starts on the agent's default interface and environment", () => {
    const state = initialLaunchForm("claude_code", "~/src", context);
    expect(state).toMatchObject({ backend: "claude_code", transport: "claude_tty", cwd: "~/src", permissionMode: "default" });
    expect(state.envEntries.map((entry) => [entry.key, entry.value])).toEqual([["CLAUDE_HOME", "/c"]]);
  });
});

describe("changeAgent", () => {
  it("clears what belongs to the old agent and keeps a shared interface", () => {
    const filled = {
      ...initialLaunchForm("claude_code", "~", context),
      transport: "tmux",
      model: "opus",
      effort: "high",
      accountProfileId: "work",
      title: "kept",
    };
    const next = changeAgent(filled, "codex", context);
    expect(next).toMatchObject({ backend: "codex", transport: "tmux", model: "", effort: "", accountProfileId: "", title: "kept" });
    expect(next.envEntries.map((entry) => entry.key)).toEqual(["CODEX_HOME"]);
  });

  it("moves to the new agent's default interface when the old one is not offered", () => {
    const next = changeAgent(initialLaunchForm("claude_code", "~", context), "codex", context);
    expect(next.transport).toBe("codex_app_server");
  });
});

describe("applyPresetSpec", () => {
  it("switches agent and fills every preset field in one step", () => {
    const state = { ...initialLaunchForm("claude_code", "~/a", context), title: "mine" };
    const next = applyPresetSpec(
      state,
      {
        backend: "codex",
        model: "gpt",
        effort: "high",
        permission_mode: "full_access",
        launch_env: { K: "v" },
        args: ["--a", "--b"],
        config_overrides: ["x=1"],
        usage_limit_source: "usage_provider",
        usage_provider_id: "lumid",
        usage_provider_account_key: "me",
      },
      context,
    );
    expect(next).toMatchObject({
      backend: "codex",
      transport: "codex_app_server",
      model: "gpt",
      effort: "high",
      permissionMode: "full_access",
      customArgsText: "--a\n--b",
      configOverridesText: "x=1",
      cwd: "~/a",
      title: "mine",
      usageSelection: { source: "usage_provider", providerId: "lumid", accountKey: "me" },
    });
    expect(next.envEntries.map((entry) => [entry.key, entry.value])).toEqual([["K", "v"]]);
  });

  it("keeps the permission mode and interface a preset leaves unset", () => {
    const state = { ...initialLaunchForm("claude_code", "~", context), permissionMode: "auto", transport: "tmux" };
    const next = applyPresetSpec(state, { model: "opus" }, context);
    expect(next).toMatchObject({ permissionMode: "auto", transport: "tmux", usageSelection: { source: "plugin" } });
  });
});

describe("clampLaunchForm", () => {
  it("drops values the agent does not offer and returns the same state otherwise", () => {
    const state = initialLaunchForm("claude_code", "~", context);
    expect(clampLaunchForm(state, context, null)).toBe(state);
    const stale = { ...state, permissionMode: "plan", accountProfileId: "gone", transport: "codex_app_server" };
    expect(clampLaunchForm(stale, context, null)).toMatchObject({
      permissionMode: "default",
      accountProfileId: "",
      transport: "claude_tty",
    });
  });

  it("keeps an effort until the model list says it is unsupported", () => {
    const state = { ...initialLaunchForm("claude_code", "~", context), model: "haiku", effort: "high" };
    expect(clampLaunchForm(state, context, null).effort).toBe("high");
    expect(clampLaunchForm(state, context, models).effort).toBe("");
  });

  it("leaves an agent the catalog does not know untouched", () => {
    const state = { ...initialLaunchForm("claude_code", "~", context), backend: "future", permissionMode: "odd" };
    expect(clampLaunchForm(state, context, null)).toBe(state);
  });
});

describe("effortOptionsFor", () => {
  it("reads the chosen or default model's levels", () => {
    expect(effortOptionsFor(null, "")).toEqual([]);
    expect(effortOptionsFor(models, "")).toEqual(["low", "high", "xhigh"]);
    expect(effortOptionsFor(models, "haiku")).toEqual([]);
    expect(effortOptionsFor(models, "sonnet")).toEqual(["low", "medium", "high"]);
  });

  it("offers every model's levels when there is no model to go by", () => {
    const noDefault = { ...models, default_model_id: null };
    expect(effortOptionsFor(noDefault, "custom")).toEqual(["low", "high", "xhigh"]);
  });
});

describe("launchCapabilities", () => {
  const caps = (backend: string, transport: string) =>
    launchCapabilities({ ...initialLaunchForm(backend, "~", context), transport }, context, models);

  it("shows the interface, account and permission controls only when offered", () => {
    expect(caps("claude_code", "claude_tty")).toMatchObject({ showInterface: true, showAccount: true, showPermission: true });
    expect(caps("opencode", "opencode_http")).toMatchObject({ showInterface: true, showAccount: false, showPermission: false });
  });

  it("hides model and effort when a terminal launch cannot carry them", () => {
    expect(caps("claude_code", "tmux")).toMatchObject({ modelSupported: true, effortSupported: true, showEffort: true });
    expect(caps("codex", "tmux")).toMatchObject({ modelSupported: true, effortSupported: false, showEffort: false });
    expect(caps("opencode", "tmux")).toMatchObject({ modelSupported: false, effortSupported: false });
    expect(caps("opencode", "opencode_http")).toMatchObject({ modelSupported: true, effortSupported: true });
  });

  it("shows both when an older catalog omits the terminal launch fields", () => {
    const old = launchCatalogDescriptors().map((descriptor) => ({
      ...descriptor,
      agent_capabilities: {
        ...descriptor.agent_capabilities,
        supports_terminal_launch_model: undefined,
        supports_terminal_launch_effort: undefined,
      },
    }));
    const oldContext = { ...context, catalog: buildCatalog(old) };
    const state = { ...initialLaunchForm("opencode", "~", oldContext), transport: "tmux" };
    expect(launchCapabilities(state, oldContext, null)).toMatchObject({ modelSupported: true, effortSupported: true });
  });
});

describe("requests", () => {
  const state = {
    ...initialLaunchForm("opencode", "/w", context),
    transport: "tmux",
    model: "gemini",
    effort: "high",
    title: "T",
    customArgsText: " --a \n\n--b",
  };
  const caps = launchCapabilities(state, context, null);

  it("leaves out what a terminal launch cannot carry, everywhere it is sent", () => {
    expect(createSessionRequest(state, caps, "studio", "p1")).toMatchObject({
      backend: "opencode",
      cwd: "/w",
      launch_target_id: "studio",
      transport: "tmux",
      title: "T",
      model: null,
      effort: null,
      args: ["--a", "--b"],
      config_overrides: [],
      preset_id: "p1",
      source_mode: "managed",
    });
    expect(scheduleLaunchRequest(state, caps, "studio", "  go ", null)).toMatchObject({
      model: null,
      effort: null,
      initial_prompt: "go",
      launch_mode: "auto",
      launch_target_id: "studio",
    });
    expect(presetSpecFromForm(state, caps)).toMatchObject({ model: null, effort: null, backend: "opencode" });
  });

  it("sends this host as a null target", () => {
    expect(createSessionRequest(state, caps, "", null).launch_target_id).toBeNull();
    expect(scheduleLaunchRequest(state, caps, null, "", null).launch_target_id).toBeNull();
  });

  it("names the Advanced settings that are set", () => {
    expect(advancedOverrides(state, caps)).toEqual(["arguments"]);
    const busy = {
      ...state,
      usageSelection: { source: "usage_provider" as const, providerId: "lumid", accountKey: "a" },
      envEntries: [{ id: 1, key: "A", value: "1" }],
    };
    expect(advancedOverrides(busy, caps)).toEqual(["usage", "environment", "arguments"]);
    expect(advancedOverrides({ ...state, customArgsText: " " }, caps)).toEqual([]);
  });
});
