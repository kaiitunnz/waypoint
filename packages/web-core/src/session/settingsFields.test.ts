import { describe, expect, it } from "vitest";

import type { BackendCapabilities, BackendModelOption, SessionLaunchSettings, TransportSettingsOption, UsageProviderOption } from "../types";
import { PLUGIN_SELECTION } from "../usageLimitSource";
import { applyErrorMessage, settingsFieldGates, type SettingsFieldGateInput } from "./settingsFields";

const caps = (patch: Partial<BackendCapabilities> = {}): BackendCapabilities =>
  ({
    supports_set_permission_mode_inline: true,
    supports_set_permission_mode_with_restart: false,
    supports_set_model_inline: true,
    supports_set_model_with_restart: false,
    supports_set_effort_inline: true,
    supports_set_effort_with_restart: false,
    ...patch,
  }) as BackendCapabilities;

const launchSettings = (patch: Partial<SessionLaunchSettings> = {}): SessionLaunchSettings =>
  ({
    account_profiles: [],
    supports_custom_args: false,
    supports_config_overrides: false,
    supports_account_profile_with_restart: false,
    supports_launch_settings_with_restart: true,
    supports_transport_switch_with_restart: false,
    transport_options: [],
    ...patch,
  }) as SessionLaunchSettings;

const model = (id: string, efforts?: string[] | null): BackendModelOption => ({ id, label: id, supported_efforts: efforts });
const provider = { id: "lumid", label: "Lumid", type: "lumid", accounts: [], status: {} } as unknown as UsageProviderOption;
const option = (id: string, profile: boolean): TransportSettingsOption => ({
  id,
  supports_launch_settings_with_restart: true,
  supports_account_profile_with_restart: profile,
  supports_custom_args: true,
  supports_config_overrides: false,
});

const input = (patch: Partial<SettingsFieldGateInput> = {}): SettingsFieldGateInput => ({
  caps: caps(),
  launchSettings: launchSettings(),
  transportOptions: [],
  transport: "claude_tty",
  transportChanged: false,
  models: [model("opus", ["low", "high"]), model("haiku", [])],
  effortLevels: ["low", "medium", "high"],
  model: "opus",
  effort: "high",
  usageProviderOptions: [],
  usageSelection: PLUGIN_SELECTION,
  launchFieldsAvailable: true,
  launchFieldsDisabledReason: null,
  session: { transport: "claude_tty" },
  openedTransport: "claude_tty",
  permissionModes: [{ id: "auto", label: "Auto" }],
  isAssistant: false,
  ...patch,
});

const gates = (patch?: Partial<SettingsFieldGateInput>) => settingsFieldGates(input(patch));

describe("tuning fields", () => {
  it("shows each knob its transport can apply, inline or with a restart", () => {
    expect(gates()).toMatchObject({ showPermission: true, showModel: true, showEffort: true });
    const restartOnly = caps({
      supports_set_permission_mode_inline: false,
      supports_set_permission_mode_with_restart: true,
      supports_set_model_inline: false,
      supports_set_model_with_restart: true,
      supports_set_effort_inline: false,
      supports_set_effort_with_restart: true,
    });
    expect(gates({ caps: restartOnly })).toMatchObject({ showPermission: true, showModel: true, showEffort: true });
  });

  it("hides a knob the transport cannot apply, and all while the capabilities are unknown", () => {
    const none = caps({ supports_set_model_inline: false, supports_set_effort_inline: false, supports_set_permission_mode_inline: false });
    expect(gates({ caps: none })).toMatchObject({ showPermission: false, showModel: false, showEffort: false });
    expect(gates({ caps: undefined })).toMatchObject({ showPermission: false, showModel: false, showEffort: false });
  });

  it("hides permission mode where the agent has no modes", () => {
    expect(gates({ permissionModes: [] }).showPermission).toBe(false);
  });

  it("hides tuning while an interface switch is staged", () => {
    expect(gates({ transportChanged: true })).toMatchObject({ showPermission: false, showModel: false, showEffort: false });
  });

  it("shows model and effort for a value the agent does not list, but not with nothing to pick", () => {
    expect(gates({ models: [], model: "custom" }).showModel).toBe(true);
    expect(gates({ models: [], model: null }).showModel).toBe(false);
    expect(gates({ models: [model("haiku", [])], model: "haiku", effort: "high" }).showEffort).toBe(true);
    expect(gates({ models: [model("haiku", [])], model: "haiku", effort: null }).showEffort).toBe(false);
  });
});

describe("effortOptions", () => {
  it("is the chosen model's own levels", () => {
    expect(gates().effortOptions).toEqual(["low", "high"]);
    expect(gates({ model: "haiku" }).effortOptions).toEqual([]);
  });

  it("is the agent's whole vocabulary for a model with unknown levels", () => {
    expect(gates({ models: [model("opus", null)] }).effortOptions).toEqual(["low", "medium", "high"]);
  });

  it("is the union of every model's levels with no model chosen, without falling back to the default", () => {
    expect(gates({ model: null }).effortOptions).toEqual(["low", "high"]);
    expect(gates({ model: null, models: [model("a", ["low"]), model("b", ["low", "max"])] }).effortOptions).toEqual(["low", "max"]);
  });
});

describe("account profile", () => {
  const withProfiles = (flag: boolean) => launchSettings({ account_profiles: [{ id: "work", label: "Work", config_dir_key: "K" }], supports_account_profile_with_restart: flag });

  it("shows where the session can switch profile", () => {
    expect(gates({ launchSettings: withProfiles(true) }).showAccountProfile).toBe(true);
    expect(gates({ launchSettings: withProfiles(false) }).showAccountProfile).toBe(false);
    expect(gates({ launchSettings: launchSettings({ supports_account_profile_with_restart: true }) }).showAccountProfile).toBe(false);
  });

  it("follows the staged interface's support while a switch is staged", () => {
    const settings = withProfiles(false);
    const options = [option("claude_tty", false), option("claude_cli", true)];
    expect(gates({ launchSettings: settings, transportOptions: options, transportChanged: true, transport: "claude_cli" }).showAccountProfile).toBe(true);
    expect(gates({ launchSettings: settings, transportOptions: options, transportChanged: true, transport: "claude_tty" }).showAccountProfile).toBe(false);
  });

  it("falls back to the loaded session's interface, then the opened one, to find the option", () => {
    const settings = withProfiles(false);
    const options = [option("claude_cli", true)];
    expect(gates({ launchSettings: settings, transportOptions: options, transportChanged: true, transport: null, session: { transport: "claude_cli" } }).showAccountProfile).toBe(true);
    expect(gates({ launchSettings: settings, transportOptions: options, transportChanged: true, transport: null, session: null, openedTransport: "claude_cli" }).showAccountProfile).toBe(true);
  });
});

describe("advanced fields", () => {
  const none = launchSettings({ supports_launch_settings_with_restart: false });
  const bare = { launchSettings: none, launchFieldsAvailable: false, launchFieldsDisabledReason: null };

  it("is hidden with nothing to edit", () => {
    expect(gates(bare).advancedVisible).toBe(false);
  });

  it.each([
    ["custom args", { launchSettings: launchSettings({ supports_custom_args: true, supports_launch_settings_with_restart: false }) }],
    ["config overrides", { launchSettings: launchSettings({ supports_config_overrides: true, supports_launch_settings_with_restart: false }) }],
    ["editable launch fields", { launchFieldsAvailable: true }],
    ["a reason the fields are locked", { launchFieldsDisabledReason: "Not yours" }],
    ["providers to read usage from", { usageProviderOptions: [provider] }],
  ])("shows for %s", (_name, patch) => {
    expect(gates({ ...bare, ...patch }).advancedVisible).toBe(true);
  });

  it("shows usage limits with no provider configured when the session already reads from one", () => {
    const selected = { source: "usage_provider" as const, providerId: "lumid", accountKey: "a" };
    expect(gates({ ...bare, usageSelection: selected })).toMatchObject({ usageSourceVisible: true, advancedVisible: true });
    expect(gates({ ...bare }).usageSourceVisible).toBe(false);
  });

  it("is hidden for the assistant", () => {
    expect(gates({ isAssistant: true, usageProviderOptions: [provider] })).toMatchObject({ advancedVisible: false, usageSourceVisible: false });
  });
});

describe("applyErrorMessage", () => {
  it("says so when an earlier step went through", () => {
    expect(applyErrorMessage("boom", false)).toBe("boom");
    expect(applyErrorMessage("boom", true)).toBe("Some changes were applied before this failed: boom");
  });
});
