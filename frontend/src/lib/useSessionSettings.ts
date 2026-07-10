"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  fetchBackendModels,
  fetchLaunchSettings,
  fetchSession,
  setSessionEffort,
  setSessionModel,
  setSessionPermissionMode,
  setSessionTitle,
  updateLaunchSettings,
} from "@/lib/api";
import type { BackendCatalog } from "@/lib/backends";
import type {
  Backend,
  BackendCapabilities,
  BackendModelOption,
  LaunchSettingsUpdate,
  SessionLaunchSettings,
  SessionRecord,
  TransportSettingsOption,
} from "@/lib/types";

// Editing model for an existing (redacted) env key. Its stored value is never
// fetched or prefilled: the user may keep it, replace it (typing a new value),
// or remove it.
export type EnvKeyOp = "keep" | "replace" | "remove";

export interface ExistingEnvEntry {
  key: string;
  op: EnvKeyOp;
  // Only meaningful when op === "replace"; empty otherwise. Never a stored value.
  value: string;
}

export interface NewEnvEntry {
  id: number;
  key: string;
  value: string;
}

export type SettingsPlanKind =
  | "none"
  | "inline"
  | "restart-resume"
  | "assistant-replace";

export interface SettingsPlan {
  kind: SettingsPlanKind;
  // How many process restarts the plan will cause across the batched launch
  // PATCH and any separately-applied tuning changes.
  restartCount: number;
  willInterruptTurn: boolean;
  warnings: string[];
}

// Assistant lifecycle actions the editor delegates to the existing controls.
// Agent/interface change and native-thread adoption create a fresh assistant
// (the reset/attach lifecycle); the modal stages the target and the controller
// dispatches it here.
export interface AssistantSettingsControls {
  onSwitchBackend: (
    backend: Backend,
    transport: string,
    accountProfileId: string | null,
  ) => Promise<void>;
  onAttachThread: (
    backend: Backend,
    threadId: string,
    accountProfileId: string | null,
  ) => Promise<void>;
}

export interface UseSessionSettingsParams {
  host: string;
  token: string;
  open: boolean;
  session: SessionRecord | null;
  catalog?: BackendCatalog;
  onApplied?: (session: SessionRecord) => void;
  onAuthFailure?: () => void;
  // Present only for the Personal Assistant; enables the replacement path.
  isAssistant?: boolean;
  assistantControls?: AssistantSettingsControls;
}

export interface SessionSettingsController {
  loading: boolean;
  loadError: string | null;
  applying: boolean;
  applyError: string | null;
  // Set after a partial-success apply so the UI can show what still needs work.
  partialSuccess: boolean;

  session: SessionRecord | null;
  launchSettings: SessionLaunchSettings | null;
  caps: BackendCapabilities | undefined;
  models: BackendModelOption[];
  defaultModelLabel: string | null;

  // Draft state.
  title: string;
  permissionMode: string | null;
  model: string | null;
  effort: string | null;
  accountProfileId: string | null;
  argsText: string;
  configOverridesText: string;
  existingEnv: ExistingEnvEntry[];
  newEnv: NewEnvEntry[];
  // Non-assistant staged interface (transport) for an in-place switch.
  transport: string | null;
  // Assistant-only staged replacement target (agent / interface / thread).
  assistantBackend: Backend | null;
  assistantTransport: string | null;
  assistantThreadId: string | null;
  assistantReplacementStaged: boolean;

  // Derived gates.
  dirty: boolean;
  plan: SettingsPlan;
  applyDisabled: boolean;
  launchFieldsAvailable: boolean;
  launchFieldsDisabledReason: string | null;
  // Interfaces this session may switch to (empty when switching isn't offered).
  transportOptions: TransportSettingsOption[];
  transportSwitchAvailable: boolean;
  transportChanged: boolean;
  // Keys hidden from raw env editing for the *staged* profile.
  hiddenEnvKeys: string[];

  // Draft mutators.
  setTitle: (value: string) => void;
  setPermissionMode: (value: string | null) => void;
  setModel: (value: string | null) => void;
  setEffort: (value: string | null) => void;
  setAccountProfileId: (value: string | null) => void;
  setArgsText: (value: string) => void;
  setConfigOverridesText: (value: string) => void;
  setExistingEnvOp: (key: string, op: EnvKeyOp) => void;
  setExistingEnvValue: (key: string, value: string) => void;
  setNewEnv: (entries: NewEnvEntry[]) => void;
  setTransport: (value: string) => void;
  setAssistantBackend: (backend: Backend) => void;
  setAssistantTransport: (transport: string) => void;
  setAssistantThreadId: (threadId: string | null) => void;

  reload: () => void;
  apply: () => Promise<boolean>;
}

function argsToText(args: string[]): string {
  return args.join("\n");
}

function textToArgs(text: string): string[] {
  return text
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line.length > 0);
}

function sameStringList(a: string[], b: string[]): boolean {
  if (a.length !== b.length) return false;
  return a.every((value, index) => value === b[index]);
}

export function useSessionSettings(
  params: UseSessionSettingsParams,
): SessionSettingsController {
  const {
    host,
    token,
    open,
    session: candidate,
    catalog,
    onApplied,
    onAuthFailure,
    isAssistant,
    assistantControls,
  } = params;

  const [loading, setLoading] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [partialSuccess, setPartialSuccess] = useState(false);

  const [session, setSession] = useState<SessionRecord | null>(candidate);
  const [launchSettings, setLaunchSettings] =
    useState<SessionLaunchSettings | null>(null);
  const [models, setModels] = useState<BackendModelOption[]>([]);
  const [defaultModelLabel, setDefaultModelLabel] = useState<string | null>(
    null,
  );

  // Draft.
  const [title, setTitle] = useState("");
  const [permissionMode, setPermissionMode] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);
  const [effort, setEffort] = useState<string | null>(null);
  const [accountProfileId, setAccountProfileId] = useState<string | null>(null);
  const [argsText, setArgsText] = useState("");
  const [configOverridesText, setConfigOverridesText] = useState("");
  const [existingEnv, setExistingEnv] = useState<ExistingEnvEntry[]>([]);
  const [newEnv, setNewEnv] = useState<NewEnvEntry[]>([]);
  const [transportState, setTransportState] = useState<string | null>(null);
  const [assistantBackend, setAssistantBackendState] = useState<Backend | null>(
    null,
  );
  const [assistantTransport, setAssistantTransportState] = useState<
    string | null
  >(null);
  const [assistantThreadId, setAssistantThreadIdState] = useState<string | null>(
    null,
  );

  // Guards a late fetch from overwriting a newer load or an in-flight apply.
  const loadToken = useRef(0);

  const sessionId = candidate?.id ?? null;
  const backend = candidate?.backend;
  const transport = candidate?.transport;
  const launchTargetId = candidate?.launch_target_id ?? null;

  // Non-assistant sessions gate every field on the *staged* interface so a
  // pending switch immediately reflects the target pair's capabilities; the
  // assistant keeps its own (current) pair — its interface change is a
  // replacement, handled separately.
  const effectiveTransport =
    !isAssistant && transportState ? transportState : transport;
  const caps = useMemo(() => {
    if (!catalog || !backend || !effectiveTransport) return undefined;
    return catalog.capsFor(backend, effectiveTransport);
  }, [catalog, backend, effectiveTransport]);

  const resetDraft = useCallback(
    (record: SessionRecord, settings: SessionLaunchSettings | null) => {
      setTitle(record.title ?? "");
      setPermissionMode(record.permission_mode ?? null);
      setModel(record.model ?? null);
      setEffort(record.effort ?? null);
      setAccountProfileId(record.account_profile_id ?? null);
      setArgsText(argsToText(settings?.args ?? record.args ?? []));
      setConfigOverridesText(
        argsToText(settings?.config_overrides ?? record.config_overrides ?? []),
      );
      const protectedKeys = new Set(settings?.protected_launch_env_keys ?? []);
      setExistingEnv(
        (settings?.launch_env_keys ?? [])
          .filter((key) => !protectedKeys.has(key))
          .map((key) => ({ key, op: "keep" as EnvKeyOp, value: "" })),
      );
      setNewEnv([]);
      setTransportState(record.transport);
      setAssistantBackendState(record.backend);
      setAssistantTransportState(record.transport);
      setAssistantThreadIdState(null);
    },
    [],
  );

  const load = useCallback(
    async (opts?: { keepMessages?: boolean }) => {
    if (!sessionId || !backend) return;
    const currentToken = ++loadToken.current;
    setLoading(true);
    setLoadError(null);
    // A post-failure refresh keeps the apply error/partial-success banner
    // visible; a normal (re)open clears it.
    if (!opts?.keepMessages) {
      setApplyError(null);
      setPartialSuccess(false);
    }
    try {
      const [record, settings] = await Promise.all([
        fetchSession(host, token, sessionId),
        fetchLaunchSettings(host, token, sessionId).catch(() => null),
      ]);
      let modelList: BackendModelOption[] = [];
      let defaultLabel: string | null = null;
      try {
        const resp = await fetchBackendModels(host, token, backend, {
          launchTargetId,
          accountProfileId: record.account_profile_id ?? null,
        });
        modelList = resp.models ?? [];
        defaultLabel = resp.default_model_label ?? null;
      } catch {
        // Model discovery is best-effort; the picker degrades to the current
        // value when it fails (e.g. a backend with no live model list).
      }
      if (currentToken !== loadToken.current) return;
      setSession(record);
      setLaunchSettings(settings);
      setModels(modelList);
      setDefaultModelLabel(defaultLabel);
      resetDraft(record, settings);
    } catch (error) {
      if (currentToken !== loadToken.current) return;
      const message =
        error instanceof Error ? error.message : "failed to load settings";
      setLoadError(message);
      if (message.toLowerCase().includes("auth")) onAuthFailure?.();
    } finally {
      if (currentToken === loadToken.current) setLoading(false);
    }
    },
    [host, token, sessionId, backend, launchTargetId, resetDraft, onAuthFailure],
  );

  useEffect(() => {
    if (open) {
      void load();
    } else {
      // Discard staged (possibly secret) state the moment the editor closes.
      loadToken.current += 1;
      setLaunchSettings(null);
      setModels([]);
      setExistingEnv([]);
      setNewEnv([]);
      setArgsText("");
      setConfigOverridesText("");
      setApplyError(null);
      setLoadError(null);
      setPartialSuccess(false);
    }
  }, [open, load]);

  // ── env editing mutators ──────────────────────────────────────────────────
  const setExistingEnvOp = useCallback((key: string, op: EnvKeyOp) => {
    setExistingEnv((prev) =>
      prev.map((entry) =>
        entry.key === key
          ? { ...entry, op, value: op === "replace" ? entry.value : "" }
          : entry,
      ),
    );
  }, []);

  const setExistingEnvValue = useCallback((key: string, value: string) => {
    setExistingEnv((prev) =>
      prev.map((entry) =>
        entry.key === key ? { ...entry, value, op: "replace" } : entry,
      ),
    );
  }, []);

  const setTransport = useCallback((value: string) => {
    setTransportState(value);
  }, []);

  const setAssistantBackend = useCallback((value: Backend) => {
    setAssistantBackendState(value);
    // Switching agent invalidates a staged interface/thread from the old agent.
    setAssistantTransportState(null);
    setAssistantThreadIdState(null);
  }, []);
  const setAssistantTransport = useCallback((value: string) => {
    setAssistantTransportState(value);
  }, []);
  const setAssistantThreadId = useCallback((value: string | null) => {
    setAssistantThreadIdState(value);
  }, []);

  // ── transport switch (non-assistant in-place interface change) ─────────────
  const transportOptions = launchSettings?.transport_options ?? [];
  const transportSwitchAvailable = Boolean(
    !isAssistant &&
      launchSettings?.supports_transport_switch_with_restart &&
      transportOptions.length > 1,
  );
  const transportChanged = Boolean(
    transportSwitchAvailable &&
      transportState !== null &&
      session !== null &&
      transportState !== session.transport,
  );
  // While a switch is staged, advanced fields follow the *target* pair's
  // restart-scoped capability; otherwise the response's own flag.
  const selectedTransportOption = transportOptions.find(
    (option) => option.id === (transportState ?? session?.transport),
  );

  // ── availability / capability gating ──────────────────────────────────────
  const launchFieldsAvailable = transportChanged
    ? Boolean(selectedTransportOption?.supports_launch_settings_with_restart)
    : Boolean(launchSettings?.supports_launch_settings_with_restart);
  const assistantReplacementStaged = Boolean(
    isAssistant &&
      session &&
      ((assistantBackend !== null && assistantBackend !== session.backend) ||
        (assistantTransport !== null &&
          assistantTransport !== session.transport) ||
        (assistantThreadId !== null && assistantThreadId.length > 0)),
  );
  const launchFieldsDisabledReason = useMemo(() => {
    if (launchFieldsAvailable) {
      if (assistantReplacementStaged) {
        return "Advanced launch settings can't be changed while switching the assistant's agent or interface.";
      }
      return null;
    }
    if (!launchSettings) return null;
    if (session?.source === "attached_tmux") {
      return "Waypoint doesn't own this attached tmux process, so its launch settings can't be edited.";
    }
    return `${backend ?? "This backend"} doesn't support restart-applied launch settings.`;
  }, [
    launchFieldsAvailable,
    assistantReplacementStaged,
    launchSettings,
    session,
    backend,
  ]);

  // The config-dir key is owned by whichever profile is *staged*; hide it from
  // raw env editing while a profile is selected.
  const hiddenEnvKeys = useMemo(() => {
    const key = launchSettings?.config_dir_env_var;
    return key && accountProfileId ? [key] : [];
  }, [launchSettings?.config_dir_env_var, accountProfileId]);

  // ── env patch computation (redaction-safe) ────────────────────────────────
  const envPatch = useMemo(() => {
    const envSet: Record<string, string> = {};
    const envUnset: string[] = [];
    const hidden = new Set(hiddenEnvKeys);
    for (const entry of existingEnv) {
      if (hidden.has(entry.key)) continue;
      if (entry.op === "replace") {
        // Only emit a value the user actually typed — never synthesize an
        // empty value for an unchanged redacted key. A blank Replace is treated
        // as "keep" so it can't silently wipe a stored secret to "".
        if (entry.value !== "") envSet[entry.key] = entry.value;
      } else if (entry.op === "remove") {
        envUnset.push(entry.key);
      }
    }
    for (const entry of newEnv) {
      const key = entry.key.trim();
      if (!key || hidden.has(key)) continue;
      envSet[key] = entry.value;
    }
    return { envSet, envUnset };
  }, [existingEnv, newEnv, hiddenEnvKeys]);

  // ── change detection ──────────────────────────────────────────────────────
  const stagedArgs = useMemo(() => textToArgs(argsText), [argsText]);
  const stagedConfig = useMemo(
    () => textToArgs(configOverridesText),
    [configOverridesText],
  );

  const titleChanged = Boolean(
    session && title.trim() && title.trim() !== session.title,
  );
  // Inline tuning is suppressed while an interface switch is staged (the target
  // pair may not support it, and it can't be applied in the same restart), so
  // these read false during a staged switch — matching the hidden controls.
  const permissionChanged = Boolean(
    session &&
      !transportChanged &&
      (permissionMode ?? null) !== (session.permission_mode ?? null),
  );
  const modelChanged = Boolean(
    session && !transportChanged && (model ?? null) !== (session.model ?? null),
  );
  const effortChanged = Boolean(
    session && !transportChanged && (effort ?? null) !== (session.effort ?? null),
  );
  const profileChanged = Boolean(
    session &&
      (accountProfileId ?? null) !== (session.account_profile_id ?? null) &&
      accountProfileId !== null,
  );
  const argsChanged = Boolean(
    launchSettings && !sameStringList(stagedArgs, launchSettings.args),
  );
  const configChanged = Boolean(
    launchSettings &&
      !sameStringList(stagedConfig, launchSettings.config_overrides),
  );
  const envChanged =
    Object.keys(envPatch.envSet).length > 0 || envPatch.envUnset.length > 0;

  const launchChanged =
    launchFieldsAvailable &&
    !assistantReplacementStaged &&
    (profileChanged || argsChanged || configChanged || envChanged);

  // Any change carried by the single launch-settings PATCH (interface switch
  // plus batched restart-scoped edits).
  const restartLaunchChanged = launchChanged || transportChanged;

  const dirty =
    titleChanged ||
    permissionChanged ||
    modelChanged ||
    effortChanged ||
    restartLaunchChanged ||
    assistantReplacementStaged;

  // ── change plan ───────────────────────────────────────────────────────────
  const plan = useMemo<SettingsPlan>(() => {
    const warnings: string[] = [];
    if (assistantReplacementStaged) {
      warnings.push(
        "The assistant conversation will be replaced and preserved as a stopped session.",
      );
      return {
        kind: "assistant-replace",
        restartCount: 0,
        willInterruptTurn: false,
        warnings,
      };
    }

    const interrupts = Boolean(caps?.settings_change_interrupts_turn);
    let tuneRestarts = 0;
    if (interrupts) {
      tuneRestarts =
        (modelChanged ? 1 : 0) +
        (effortChanged ? 1 : 0) +
        (permissionChanged ? 1 : 0);
    } else {
      if (
        effortChanged &&
        caps?.supports_set_effort_with_restart &&
        !caps?.supports_set_effort_inline
      ) {
        tuneRestarts += 1;
      }
    }
    const restartCount = (restartLaunchChanged ? 1 : 0) + tuneRestarts;
    const running =
      session?.status === "running" || session?.status === "waiting_input";
    const willInterruptTurn = restartCount > 0 && running;

    if (transportChanged) {
      warnings.push("Restarts the session.");
    } else if (restartCount > 1) {
      warnings.push(
        `Applying these changes will restart the session ${restartCount} times and resume it.`,
      );
    } else if (restartCount === 1) {
      warnings.push("The session process will restart and resume.");
    }
    if (willInterruptTurn) {
      warnings.push("The current turn will be interrupted.");
    }
    if (profileChanged) {
      warnings.push(
        "Switching account profile restarts the session under the new profile. For Codex, a thread with no persisted transcript starts fresh.",
      );
    }

    let kind: SettingsPlanKind = "none";
    if (restartCount > 0) kind = "restart-resume";
    else if (dirty) kind = "inline";
    return { kind, restartCount, willInterruptTurn, warnings };
  }, [
    assistantReplacementStaged,
    caps,
    modelChanged,
    effortChanged,
    permissionChanged,
    restartLaunchChanged,
    transportChanged,
    profileChanged,
    session,
    dirty,
  ]);

  const applyDisabled = loading || applying || !dirty;

  // ── apply ─────────────────────────────────────────────────────────────────
  const apply = useCallback(async (): Promise<boolean> => {
    if (!session || !sessionId) return false;
    setApplying(true);
    setApplyError(null);
    setPartialSuccess(false);
    // Freeze the load token so a stray refresh mid-apply can't clobber results.
    const applyToken = ++loadToken.current;
    let anySucceeded = false;
    try {
      // 1. Title first — a title failure stops before any process-changing work.
      if (titleChanged) {
        const updated = await setSessionTitle(
          host,
          token,
          sessionId,
          title.trim(),
        );
        anySucceeded = true;
        setSession(updated);
        onApplied?.(updated);
      }

      // 2. Assistant replacement takes the place of the launch/tune steps: a
      //    native-thread adoption when a thread is staged, otherwise an
      //    agent/interface switch. Both create a fresh assistant and remount.
      if (assistantReplacementStaged) {
        if (!assistantControls) {
          throw new Error("assistant controls unavailable");
        }
        const targetBackend = assistantBackend ?? session.backend;
        if (assistantThreadId) {
          await assistantControls.onAttachThread(
            targetBackend,
            assistantThreadId,
            accountProfileId,
          );
        } else {
          const targetTransport = assistantTransport ?? session.transport;
          await assistantControls.onSwitchBackend(
            targetBackend,
            targetTransport,
            accountProfileId,
          );
        }
        return true;
      }

      // 3. The interface switch and all restart-scoped launch changes in one
      //    PATCH.
      if (restartLaunchChanged) {
        const update: LaunchSettingsUpdate = { restart: true };
        if (transportChanged && transportState) update.transport = transportState;
        if (profileChanged) update.account_profile_id = accountProfileId;
        if (argsChanged) update.args = stagedArgs;
        if (configChanged) update.config_overrides = stagedConfig;
        if (Object.keys(envPatch.envSet).length > 0) {
          update.env_set = envPatch.envSet;
        }
        if (envPatch.envUnset.length > 0) update.env_unset = envPatch.envUnset;
        const updated = await updateLaunchSettings(
          host,
          token,
          sessionId,
          update,
        );
        anySucceeded = true;
        setSession(updated);
        onApplied?.(updated);
      }

      // 4. Remaining inline tuning via their own endpoints: permission → model
      //    → effort. Refresh after each.
      if (permissionChanged && permissionMode !== null) {
        const updated = await setSessionPermissionMode(
          host,
          token,
          sessionId,
          permissionMode,
        );
        anySucceeded = true;
        setSession(updated);
        onApplied?.(updated);
      }
      if (modelChanged) {
        const updated = await setSessionModel(host, token, sessionId, model);
        anySucceeded = true;
        setSession(updated);
        onApplied?.(updated);
      }
      if (effortChanged) {
        const updated = await setSessionEffort(host, token, sessionId, effort);
        anySucceeded = true;
        setSession(updated);
        onApplied?.(updated);
      }
      return true;
    } catch (error) {
      const message =
        error instanceof Error ? error.message : "failed to apply settings";
      setApplyError(message);
      // A later independent op failed after an earlier one succeeded: surface a
      // truthful partial-success state and refresh, never roll back. The refresh
      // must keep the error banner (load() otherwise clears it synchronously).
      setPartialSuccess(anySucceeded);
      if (message.toLowerCase().includes("auth")) onAuthFailure?.();
      if (applyToken === loadToken.current) void load({ keepMessages: true });
      return false;
    } finally {
      setApplying(false);
    }
  }, [
    session,
    sessionId,
    host,
    token,
    titleChanged,
    title,
    assistantReplacementStaged,
    assistantControls,
    assistantBackend,
    assistantTransport,
    assistantThreadId,
    accountProfileId,
    restartLaunchChanged,
    transportChanged,
    transportState,
    profileChanged,
    argsChanged,
    stagedArgs,
    configChanged,
    stagedConfig,
    envPatch,
    permissionChanged,
    permissionMode,
    modelChanged,
    model,
    effortChanged,
    effort,
    onApplied,
    onAuthFailure,
    load,
  ]);

  return {
    loading,
    loadError,
    applying,
    applyError,
    partialSuccess,
    session,
    launchSettings,
    caps,
    models,
    defaultModelLabel,
    title,
    permissionMode,
    model,
    effort,
    accountProfileId,
    argsText,
    configOverridesText,
    existingEnv,
    newEnv,
    transport: transportState,
    assistantBackend,
    assistantTransport,
    assistantThreadId,
    assistantReplacementStaged,
    dirty,
    plan,
    applyDisabled,
    launchFieldsAvailable,
    launchFieldsDisabledReason,
    transportOptions,
    transportSwitchAvailable,
    transportChanged,
    hiddenEnvKeys,
    setTitle,
    setPermissionMode,
    setModel,
    setEffort,
    setAccountProfileId,
    setArgsText,
    setConfigOverridesText,
    setExistingEnvOp,
    setExistingEnvValue,
    setNewEnv,
    setTransport,
    setAssistantBackend,
    setAssistantTransport,
    setAssistantThreadId,
    reload: load,
    apply,
  };
}
