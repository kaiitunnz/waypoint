import type { SessionSettingsController } from "../hooks/useSessionSettings";
import type { AccountProfile, BackendPermissionMode, SessionRecord } from "../types";

// The controller's state the gates read.
type GateState = Pick<
  SessionSettingsController,
  | "caps"
  | "launchSettings"
  | "transportOptions"
  | "transport"
  | "transportChanged"
  | "models"
  | "effortLevels"
  | "model"
  | "effort"
  | "usageProviderOptions"
  | "usageSelection"
  | "launchFieldsAvailable"
  | "launchFieldsDisabledReason"
>;

export interface SettingsFieldGateInput extends GateState {
  // The session as the controller last loaded it.
  session: Pick<SessionRecord, "transport"> | null;
  // The transport the session had when the editor opened.
  openedTransport: string;
  permissionModes: readonly BackendPermissionMode[];
  isAssistant: boolean;
}

export interface SettingsFieldGates {
  showPermission: boolean;
  showModel: boolean;
  showEffort: boolean;
  showAccountProfile: boolean;
  // Usage limits from: also where the session has no editable launch fields.
  usageSourceVisible: boolean;
  advancedVisible: boolean;
  // The efforts to offer for the chosen model, else every model's.
  effortOptions: string[];
  accountProfiles: AccountProfile[];
}

// Which session-settings fields a session offers. Tuning is hidden, not disabled, where
// its transport cannot apply it, and while an interface switch is staged, which cannot
// ride the same restart.
export function settingsFieldGates(input: SettingsFieldGateInput): SettingsFieldGates {
  const {
    caps,
    launchSettings,
    transportOptions,
    transport,
    transportChanged,
    models,
    effortLevels,
    model,
    effort,
    usageProviderOptions,
    usageSelection,
    launchFieldsAvailable,
    launchFieldsDisabledReason,
    session,
    openedTransport,
    permissionModes,
    isAssistant,
  } = input;

  const selectedModel = models.find((option) => option.id === (model ?? ""));
  const effortOptions = selectedModel
    ? // null/undefined efforts mean unknown: offer the agent's full vocabulary.
      selectedModel.supported_efforts == null
      ? effortLevels
      : selectedModel.supported_efforts
    : Array.from(new Set(models.flatMap((option) => option.supported_efforts ?? [])));
  const accountProfiles = launchSettings?.account_profiles ?? [];
  // The option for the interface the session has or will have; it drives the restart-scoped gates.
  const activeTransportOption = transportOptions.find((option) => option.id === (transport ?? session?.transport ?? openedTransport));

  const showPermission =
    !transportChanged &&
    Boolean(caps?.supports_set_permission_mode_inline || caps?.supports_set_permission_mode_with_restart) &&
    permissionModes.length > 0;
  const showModel =
    !transportChanged &&
    Boolean(caps?.supports_set_model_inline || caps?.supports_set_model_with_restart) &&
    (models.length > 0 || model !== null);
  const showEffort =
    !transportChanged &&
    Boolean(caps?.supports_set_effort_inline || caps?.supports_set_effort_with_restart) &&
    (effortOptions.length > 0 || effort !== null);
  const showAccountProfile =
    accountProfiles.length > 0 &&
    Boolean(transportChanged ? activeTransportOption?.supports_account_profile_with_restart : launchSettings?.supports_account_profile_with_restart);
  const usageSourceVisible = !isAssistant && (usageProviderOptions.length > 0 || usageSelection.source === "usage_provider");
  const advancedVisible =
    !isAssistant &&
    Boolean(
      launchSettings?.supports_custom_args ||
        launchSettings?.supports_config_overrides ||
        launchFieldsAvailable ||
        launchFieldsDisabledReason !== null ||
        usageSourceVisible,
    );

  return { showPermission, showModel, showEffort, showAccountProfile, usageSourceVisible, advancedVisible, effortOptions, accountProfiles };
}

// What the editor says when applying fails: after a step has gone through, the failure
// is not the whole story.
export function applyErrorMessage(error: string, partialSuccess: boolean): string {
  return `${partialSuccess ? "Some changes were applied before this failed: " : ""}${error}`;
}
