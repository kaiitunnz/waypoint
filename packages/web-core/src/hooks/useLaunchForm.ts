"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { BackendCatalog } from "../backends";
import type { EnvEntry } from "../launch/env";
import {
  applyPresetSpec,
  changeAgent,
  clampLaunchForm,
  collectLaunchArgs,
  createSessionRequest,
  initialLaunchForm,
  launchCapabilities,
  presetSpecFromForm,
  resetAgentScope,
  scheduleLaunchRequest,
  type LaunchArgs,
  type LaunchCapabilities,
  type LaunchFormContext,
  type LaunchFormState,
} from "../launch/form";
import type {
  AccountProfile,
  Backend,
  BackendModelListResponse,
  BackendPermissionMode,
  ScheduleCreateRequest,
  SessionPresetSpec,
  SessionTransport,
  UsageProviderOption,
} from "../types";
import type { UsageLimitSourceValue } from "../usageLimitSource";

export interface UseLaunchFormParams {
  defaultBackend: Backend;
  defaultCwd: string;
  launchTargetId: string | null;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
  usageProviderOptions: UsageProviderOption[];
  catalog: BackendCatalog;
  // Fills the form as it opens, for a preset whose spec is at hand.
  initialPreset?: SessionPresetSpec | null;
}

// The launch form behind New and Schedule, over the pure state in launch/form.
export interface LaunchForm {
  state: LaunchFormState;
  caps: LaunchCapabilities;
  backend: Backend;
  cwd: string;
  setCwd: (value: string) => void;
  title: string;
  setTitle: (value: string) => void;
  model: string;
  setModel: (value: string) => void;
  effort: string;
  setEffort: (value: string) => void;
  transport: SessionTransport;
  setTransport: (value: SessionTransport) => void;
  transports: SessionTransport[];
  permissionMode: string;
  setPermissionMode: (value: string) => void;
  accountProfileId: string;
  setAccountProfileId: (value: string) => void;
  accountProfiles: AccountProfile[];
  customArgsText: string;
  setCustomArgsText: (value: string) => void;
  configOverridesText: string;
  setConfigOverridesText: (value: string) => void;
  envEntries: EnvEntry[];
  setEnvEntries: (entries: EnvEntry[]) => void;
  modelInfo: BackendModelListResponse | null;
  permissionOptions: readonly BackendPermissionMode[];
  supportsCustomArgs: boolean;
  supportsConfigOverrides: boolean;
  modelSupported: boolean;
  effortSupported: boolean;
  effortOptions: string[];
  usageProviderOptions: UsageProviderOption[];
  usageSelection: UsageLimitSourceValue;
  setUsageSelection: (value: UsageLimitSourceValue) => void;
  changeBackend: (backend: Backend) => void;
  applyPreset: (spec: SessionPresetSpec) => void;
  handleModelsLoaded: (response: BackendModelListResponse) => void;
  collectArgs: () => LaunchArgs;
  presetSpec: () => SessionPresetSpec;
  createRequest: (launchTargetId: string, presetId: string | null) => Record<string, unknown>;
  scheduleRequest: (initialPrompt: string, presetId: string | null) => ScheduleCreateRequest;
}

export function useLaunchForm({
  defaultBackend,
  defaultCwd,
  launchTargetId,
  defaultLaunchEnvByBackend,
  accountProfilesByBackend,
  usageProviderOptions,
  catalog,
  initialPreset,
}: UseLaunchFormParams): LaunchForm {
  const context = useMemo<LaunchFormContext>(
    () => ({ catalog, defaultLaunchEnvByBackend, accountProfilesByBackend }),
    [catalog, defaultLaunchEnvByBackend, accountProfilesByBackend],
  );
  const contextRef = useRef(context);
  useEffect(() => {
    contextRef.current = context;
  });
  const [state, setState] = useState<LaunchFormState>(() => {
    const initial = initialLaunchForm(defaultBackend, defaultCwd, context);
    return initialPreset ? applyPresetSpec(initial, initialPreset, context) : initial;
  });
  const stateRef = useRef(state);
  useEffect(() => {
    stateRef.current = state;
  });
  // A model list belongs to the agent it was loaded for; another agent has none yet.
  const [loadedModels, setModelInfo] = useState<{ backend: Backend; response: BackendModelListResponse } | null>(null);
  const modelInfo = loadedModels?.backend === state.backend ? loadedModels.response : null;

  const patch = useCallback((next: Partial<LaunchFormState>) => setState((current) => ({ ...current, ...next })), []);

  // A new default agent (another target, or the catalog arriving) restarts the
  // form on it; so does the target's environment changing under the same agent.
  // Each reset compares with the values it last saw, so a new object with the
  // same defaults (a target's connect state changing) keeps what the person
  // filled in, and an effect run twice resets nothing.
  const envKey = JSON.stringify(defaultLaunchEnvByBackend);
  const seen = useRef({ defaultBackend, envKey, launchTargetId, defaultCwd });
  useEffect(() => {
    if (seen.current.defaultBackend === defaultBackend && seen.current.envKey === envKey) return;
    seen.current = { ...seen.current, defaultBackend, envKey };
    setState((current) => changeAgent(current, defaultBackend, contextRef.current));
  }, [defaultBackend, envKey]);

  useEffect(() => {
    if (seen.current.launchTargetId === launchTargetId) return;
    seen.current = { ...seen.current, launchTargetId };
    setState((current) => resetAgentScope(current, contextRef.current));
    setModelInfo(null);
  }, [launchTargetId]);

  useEffect(() => {
    if (seen.current.defaultCwd === defaultCwd) return;
    seen.current = { ...seen.current, defaultCwd };
    patch({ cwd: defaultCwd });
  }, [defaultCwd, patch]);

  // Keep the form within what the agent, interface and model offer, including
  // when the catalog or the model list arrives late.
  useEffect(() => {
    setState((current) => clampLaunchForm(current, context, modelInfo));
  }, [state, context, modelInfo]);

  const caps = useMemo(() => launchCapabilities(state, context, modelInfo), [state, context, modelInfo]);

  const changeBackend = useCallback((backend: Backend) => {
    setState((current) => changeAgent(current, backend, contextRef.current));
  }, []);

  const applyPreset = useCallback((spec: SessionPresetSpec) => {
    setState((current) => applyPresetSpec(current, spec, contextRef.current));
  }, []);

  // The list arrives for the agent selected when it was asked for.
  const handleModelsLoaded = useCallback(
    (response: BackendModelListResponse) => setModelInfo({ backend: stateRef.current.backend, response }),
    [],
  );
  const setCwd = useCallback((cwd: string) => patch({ cwd }), [patch]);
  const setTitle = useCallback((title: string) => patch({ title }), [patch]);
  const setModel = useCallback((model: string) => patch({ model }), [patch]);
  const setEffort = useCallback((effort: string) => patch({ effort }), [patch]);
  const setTransport = useCallback((transport: SessionTransport) => patch({ transport }), [patch]);
  const setPermissionMode = useCallback((permissionMode: string) => patch({ permissionMode }), [patch]);
  const setAccountProfileId = useCallback((accountProfileId: string) => patch({ accountProfileId }), [patch]);
  const setCustomArgsText = useCallback((customArgsText: string) => patch({ customArgsText }), [patch]);
  const setConfigOverridesText = useCallback(
    (configOverridesText: string) => patch({ configOverridesText }),
    [patch],
  );
  const setEnvEntries = useCallback((envEntries: EnvEntry[]) => patch({ envEntries }), [patch]);
  const setUsageSelection = useCallback((usageSelection: UsageLimitSourceValue) => patch({ usageSelection }), [patch]);

  const collectArgs = useCallback(() => collectLaunchArgs(state, caps), [state, caps]);
  const presetSpec = useCallback(() => presetSpecFromForm(state, caps), [state, caps]);
  const createRequest = useCallback(
    (targetId: string, presetId: string | null) => createSessionRequest(state, caps, targetId, presetId),
    [state, caps],
  );
  const scheduleRequest = useCallback(
    (initialPrompt: string, presetId: string | null) => scheduleLaunchRequest(state, caps, initialPrompt, presetId),
    [state, caps],
  );

  return {
    state,
    caps,
    backend: state.backend,
    cwd: state.cwd,
    setCwd,
    title: state.title,
    setTitle,
    model: state.model,
    setModel,
    effort: state.effort,
    setEffort,
    transport: state.transport,
    setTransport,
    transports: caps.transports,
    permissionMode: state.permissionMode,
    setPermissionMode,
    accountProfileId: state.accountProfileId,
    setAccountProfileId,
    accountProfiles: caps.accountProfiles,
    customArgsText: state.customArgsText,
    setCustomArgsText,
    configOverridesText: state.configOverridesText,
    setConfigOverridesText,
    envEntries: state.envEntries,
    setEnvEntries,
    modelInfo,
    permissionOptions: caps.permissionOptions,
    supportsCustomArgs: caps.supportsCustomArgs,
    supportsConfigOverrides: caps.supportsConfigOverrides,
    modelSupported: caps.modelSupported,
    effortSupported: caps.effortSupported,
    effortOptions: caps.effortOptions,
    usageProviderOptions,
    usageSelection: state.usageSelection,
    setUsageSelection,
    changeBackend,
    applyPreset,
    handleModelsLoaded,
    collectArgs,
    presetSpec,
    createRequest,
    scheduleRequest,
  };
}
