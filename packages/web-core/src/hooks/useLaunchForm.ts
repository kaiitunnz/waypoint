"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import type { BackendCatalog } from "../backends";
import type { EnvEntry } from "../launch/env";
import {
  applyPresetSpec,
  changeAgent,
  clampLaunchForm,
  createSessionRequest,
  initialLaunchForm,
  launchCapabilities,
  presetSpecFromForm,
  resetAgentScope,
  scheduleLaunchRequest,
  type LaunchCapabilities,
  type LaunchFormContext,
  type LaunchFormState,
} from "../launch/form";
import type {
  AccountProfile,
  Backend,
  BackendModelListResponse,
  ScheduleCreateRequest,
  SessionCreateRequest,
  SessionPresetSpec,
  SessionTransport,
  UsageProviderOption,
} from "../types";
import type { UsageLimitSourceValue } from "../usageLimitSource";
import { useLaunchModels } from "./useLaunchModels";

export interface UseLaunchFormParams {
  host: string;
  token: string;
  // Must be stable.
  onAuthFailure: () => void;
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
  permissionMode: string;
  setPermissionMode: (value: string) => void;
  accountProfileId: string;
  setAccountProfileId: (value: string) => void;
  customArgsText: string;
  setCustomArgsText: (value: string) => void;
  configOverridesText: string;
  setConfigOverridesText: (value: string) => void;
  envEntries: EnvEntry[];
  setEnvEntries: (entries: EnvEntry[]) => void;
  modelInfo: BackendModelListResponse | null;
  usageProviderOptions: UsageProviderOption[];
  usageSelection: UsageLimitSourceValue;
  setUsageSelection: (value: UsageLimitSourceValue) => void;
  changeBackend: (backend: Backend) => void;
  applyPreset: (spec: SessionPresetSpec) => void;
  presetSpec: () => SessionPresetSpec;
  createRequest: (presetId: string | null) => SessionCreateRequest;
  scheduleRequest: (initialPrompt: string, presetId: string | null) => ScheduleCreateRequest;
}

export function useLaunchForm({
  host,
  token,
  onAuthFailure,
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
  const modelInfo = useLaunchModels(
    host,
    token,
    state.backend,
    launchTargetId,
    state.accountProfileId || null,
    onAuthFailure,
  );

  const patch = useCallback((next: Partial<LaunchFormState>) => setState((current) => ({ ...current, ...next })), []);

  // A new default agent or default environment restarts the form on it. Each
  // reset compares with the value it last saw, so new objects with the same
  // defaults keep what the person filled in.
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

  const presetSpec = useCallback(() => presetSpecFromForm(state, caps), [state, caps]);
  const createRequest = useCallback(
    (presetId: string | null) => createSessionRequest(state, caps, launchTargetId, presetId),
    [state, caps, launchTargetId],
  );
  const scheduleRequest = useCallback(
    (initialPrompt: string, presetId: string | null) =>
      scheduleLaunchRequest(state, caps, launchTargetId, initialPrompt, presetId),
    [state, caps, launchTargetId],
  );

  return {
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
    permissionMode: state.permissionMode,
    setPermissionMode,
    accountProfileId: state.accountProfileId,
    setAccountProfileId,
    customArgsText: state.customArgsText,
    setCustomArgsText,
    configOverridesText: state.configOverridesText,
    setConfigOverridesText,
    envEntries: state.envEntries,
    setEnvEntries,
    modelInfo,
    usageProviderOptions,
    usageSelection: state.usageSelection,
    setUsageSelection,
    changeBackend,
    applyPreset,
    presetSpec,
    createRequest,
    scheduleRequest,
  };
}
