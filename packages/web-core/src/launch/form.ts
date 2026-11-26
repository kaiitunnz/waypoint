import { agentTransports, defaultTransportFor, permissionModesFor, type BackendCatalog } from "../backends";
import type {
  AccountProfile,
  Backend,
  BackendModelListResponse,
  BackendPermissionMode,
  ScheduleCreateRequest,
  SessionPresetSpec,
  SessionTransport,
} from "../types";
import { PLUGIN_SELECTION, type UsageLimitSourceValue } from "../usageLimitSource";
import { entriesToRecord, recordToEntries, type EnvEntry } from "./env";

// The fields a launch shares across New and Schedule: the agent and how it runs,
// where, and the CLI passthrough. cwd and title are per launch; the rest can come
// from a preset.
export interface LaunchFormState {
  backend: Backend;
  transport: SessionTransport;
  accountProfileId: string;
  cwd: string;
  title: string;
  permissionMode: string;
  model: string;
  effort: string;
  customArgsText: string;
  configOverridesText: string;
  envEntries: EnvEntry[];
  // Independent of the agent, so agent and interface changes keep it.
  usageSelection: UsageLimitSourceValue;
}

// What the selected host or launch target offers.
export interface LaunchFormContext {
  catalog: BackendCatalog;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  // Target-merged when a launch target is active.
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
}

export function initialLaunchForm(defaultBackend: Backend, defaultCwd: string, context: LaunchFormContext): LaunchFormState {
  return {
    backend: defaultBackend,
    transport: defaultTransportFor(defaultBackend, context.catalog) ?? "",
    accountProfileId: "",
    cwd: defaultCwd,
    title: "",
    permissionMode: "default",
    model: "",
    effort: "",
    customArgsText: "",
    configOverridesText: "",
    envEntries: recordToEntries(context.defaultLaunchEnvByBackend[defaultBackend]),
    usageSelection: PLUGIN_SELECTION,
  };
}

// The current transport while the agent offers it, else the agent's default.
export function clampTransport(current: SessionTransport, backend: Backend, catalog: BackendCatalog): SessionTransport {
  const transports = agentTransports(backend, catalog);
  if (transports.length === 0 || transports.includes(current)) return current;
  return defaultTransportFor(backend, catalog) ?? transports[0] ?? "";
}

// Clears what belongs to one agent: models, effort levels and profiles do not
// carry over, and the environment returns to the agent's defaults.
export function resetAgentScope(state: LaunchFormState, context: LaunchFormContext): LaunchFormState {
  return {
    ...state,
    model: "",
    effort: "",
    accountProfileId: "",
    envEntries: recordToEntries(context.defaultLaunchEnvByBackend[state.backend]),
  };
}

export function changeAgent(state: LaunchFormState, backend: Backend, context: LaunchFormContext): LaunchFormState {
  return resetAgentScope(
    { ...state, backend, transport: clampTransport(state.transport, backend, context.catalog) },
    context,
  );
}

// Fills the form from a preset. Presets carry launch defaults only, so cwd and
// title stay as they are.
export function applyPresetSpec(state: LaunchFormState, spec: SessionPresetSpec, context: LaunchFormContext): LaunchFormState {
  const backend = (spec.backend as Backend | null | undefined) ?? state.backend;
  const base = backend === state.backend ? state : changeAgent(state, backend, context);
  return {
    ...base,
    permissionMode: spec.permission_mode || base.permissionMode,
    customArgsText: (spec.args ?? []).join("\n"),
    configOverridesText: (spec.config_overrides ?? []).join("\n"),
    usageSelection:
      spec.usage_limit_source === "usage_provider"
        ? {
            source: "usage_provider",
            providerId: spec.usage_provider_id ?? null,
            accountKey: spec.usage_provider_account_key ?? null,
          }
        : PLUGIN_SELECTION,
    model: spec.model ?? "",
    effort: spec.effort ?? "",
    accountProfileId: spec.account_profile_id ?? "",
    envEntries: recordToEntries(spec.launch_env ?? {}),
    transport: spec.transport || base.transport,
  };
}

// The effort levels for the chosen model, or for the agent's default model.
// Unknown levels on a model offer the agent's whole vocabulary; with no model to
// go by, the union of every model's levels.
export function effortOptionsFor(modelInfo: BackendModelListResponse | null, model: string): string[] {
  if (!modelInfo) return [];
  const resolved = model || modelInfo.default_model_id;
  if (resolved) {
    const option = modelInfo.models.find((entry) => entry.id === resolved);
    if (option) {
      return option.supported_efforts == null ? (modelInfo.effort_levels ?? []) : option.supported_efforts;
    }
  }
  const union = new Set<string>();
  for (const entry of modelInfo.models) {
    for (const level of entry.supported_efforts ?? []) union.add(level);
  }
  return [...union];
}

// Over the terminal transport a launch carries only what the agent's launch
// flags pin; structured transports set every field per session.
export function isTerminalLaunch(transport: SessionTransport, catalog: BackendCatalog): boolean {
  return Boolean(transport) && catalog.transportCaps(transport)?.is_structured === false;
}

export interface LaunchCapabilities {
  transports: SessionTransport[];
  showInterface: boolean;
  accountProfiles: AccountProfile[];
  showAccount: boolean;
  permissionOptions: readonly BackendPermissionMode[];
  showPermission: boolean;
  modelSupported: boolean;
  effortSupported: boolean;
  effortOptions: string[];
  showEffort: boolean;
  supportsCustomArgs: boolean;
  supportsConfigOverrides: boolean;
}

export function launchCapabilities(
  state: LaunchFormState,
  context: LaunchFormContext,
  modelInfo: BackendModelListResponse | null,
): LaunchCapabilities {
  const { catalog } = context;
  const transports = agentTransports(state.backend, catalog);
  const accountProfiles = context.accountProfilesByBackend[state.backend] ?? [];
  const permissionOptions = permissionModesFor(state.backend, catalog);
  const agent = catalog.agentCaps(state.backend);
  const terminal = isTerminalLaunch(state.transport, catalog);
  const modelSupported = !(terminal && agent?.supports_terminal_launch_model === false);
  const effortSupported = !(terminal && agent?.supports_terminal_launch_effort === false);
  const effortOptions = effortOptionsFor(modelInfo, state.model);
  const flat = catalog.byId(state.backend)?.capabilities;
  return {
    transports,
    showInterface: transports.length > 1,
    accountProfiles,
    showAccount: accountProfiles.length > 0,
    permissionOptions,
    showPermission: permissionOptions.length > 0,
    modelSupported,
    effortSupported,
    effortOptions,
    showEffort: effortSupported && effortOptions.length > 0,
    supportsCustomArgs: flat?.supports_custom_cli_args ?? false,
    supportsConfigOverrides: flat?.supports_config_overrides ?? false,
  };
}

// Drops values the current agent, transport or model no longer offers. Returns
// `state` itself when nothing changes.
export function clampLaunchForm(
  state: LaunchFormState,
  context: LaunchFormContext,
  modelInfo: BackendModelListResponse | null,
): LaunchFormState {
  // An agent the catalog does not describe yet has unknown options: keep its
  // values until the catalog arrives.
  if (!context.catalog.byId(state.backend)) return state;
  const caps = launchCapabilities(state, context, modelInfo);
  const transport = clampTransport(state.transport, state.backend, context.catalog);
  const permissionMode = caps.permissionOptions.some((option) => option.id === state.permissionMode)
    ? state.permissionMode
    : (caps.permissionOptions[0]?.id ?? "default");
  // Wait for the model list: before it loads no level can be told invalid, and a
  // preset's effort must survive until its model arrives.
  const effort = modelInfo && state.effort && !caps.effortOptions.includes(state.effort) ? "" : state.effort;
  const accountProfileId =
    state.accountProfileId && !caps.accountProfiles.some((profile) => profile.id === state.accountProfileId)
      ? ""
      : state.accountProfileId;
  if (
    transport === state.transport &&
    permissionMode === state.permissionMode &&
    effort === state.effort &&
    accountProfileId === state.accountProfileId
  ) {
    return state;
  }
  return { ...state, transport, permissionMode, effort, accountProfileId };
}

const lines = (text: string) =>
  text
    .split("\n")
    .map((value) => value.trim())
    .filter(Boolean);

export interface LaunchArgs {
  args: string[];
  configOverrides: string[];
  launchEnv: Record<string, string>;
}

export function collectLaunchArgs(state: LaunchFormState, caps: LaunchCapabilities): LaunchArgs {
  return {
    args: caps.supportsCustomArgs ? lines(state.customArgsText) : [],
    configOverrides: caps.supportsConfigOverrides ? lines(state.configOverridesText) : [],
    launchEnv: entriesToRecord(state.envEntries),
  };
}

function modelValue(state: LaunchFormState, caps: LaunchCapabilities): string | null {
  return caps.modelSupported ? state.model.trim() || null : null;
}

function effortValue(state: LaunchFormState, caps: LaunchCapabilities): string | null {
  return caps.effortSupported ? state.effort.trim() || null : null;
}

// What a preset saved from this form pins: everything but cwd, title and the
// launch target, which belong to one launch.
export function presetSpecFromForm(state: LaunchFormState, caps: LaunchCapabilities): SessionPresetSpec {
  const { args, configOverrides, launchEnv } = collectLaunchArgs(state, caps);
  return {
    backend: state.backend,
    transport: state.transport || null,
    model: modelValue(state, caps),
    effort: effortValue(state, caps),
    permission_mode: state.permissionMode || null,
    account_profile_id: state.accountProfileId || null,
    usage_limit_source: state.usageSelection.source,
    usage_provider_id: state.usageSelection.providerId,
    usage_provider_account_key: state.usageSelection.accountKey,
    args,
    config_overrides: configOverrides,
    launch_env: launchEnv,
  };
}

// The body of `POST /api/sessions`.
export function createSessionRequest(
  state: LaunchFormState,
  caps: LaunchCapabilities,
  launchTargetId: string,
  presetId: string | null,
): Record<string, unknown> {
  const { args, configOverrides, launchEnv } = collectLaunchArgs(state, caps);
  return {
    backend: state.backend,
    cwd: state.cwd,
    launch_target_id: launchTargetId || null,
    transport: state.transport || null,
    title: state.title || null,
    source_mode: "managed",
    args,
    config_overrides: configOverrides,
    launch_env: launchEnv,
    model: modelValue(state, caps),
    effort: effortValue(state, caps),
    permission_mode: state.permissionMode || null,
    account_profile_id: state.accountProfileId || null,
    usage_limit_source: state.usageSelection.source,
    usage_provider_id: state.usageSelection.providerId,
    usage_provider_account_key: state.usageSelection.accountKey,
    // Provenance only: the fields above are already resolved.
    preset_id: presetId,
  };
}

// The launch half of `POST /api/schedules`; the timing comes from scheduleTiming.
export function scheduleLaunchRequest(
  state: LaunchFormState,
  caps: LaunchCapabilities,
  initialPrompt: string,
  presetId: string | null,
): ScheduleCreateRequest {
  const { args, configOverrides, launchEnv } = collectLaunchArgs(state, caps);
  return {
    backend: state.backend,
    cwd: state.cwd,
    // An explicit transport supersedes launch_mode, so the mode stays "auto".
    launch_mode: "auto",
    transport: state.transport || null,
    title: state.title.trim() || null,
    initial_prompt: initialPrompt.trim() || null,
    permission_mode: state.permissionMode || null,
    model: modelValue(state, caps),
    effort: effortValue(state, caps),
    args,
    config_overrides: configOverrides,
    launch_env: launchEnv,
    preset_id: presetId,
    account_profile_id: state.accountProfileId || null,
    usage_limit_source: state.usageSelection.source,
    usage_provider_id: state.usageSelection.providerId,
    usage_provider_account_key: state.usageSelection.accountKey,
  };
}
