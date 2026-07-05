"use client";

import {
  Dispatch,
  SetStateAction,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import {
  AgentTransportPicker,
  useTransportForAgent,
} from "@/components/AgentTransportPicker";
import { EffortPicker } from "@/components/EffortPicker";
import {
  formatLaunchEnv,
  LaunchOptionsDetails,
  parseLaunchEnv,
} from "@/components/LaunchOptions";
import { ModelPicker } from "@/components/ModelPicker";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import { permissionModesFor } from "@/lib/backends";
import {
  Backend,
  BackendModelListResponse,
  SessionPresetSpec,
  SessionTransport,
} from "@/lib/types";

interface UseLaunchFormParams {
  defaultBackend: Backend;
  defaultCwd: string;
  launchTargetId: string | null;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  catalog: BackendCatalog;
}

// The shared launch-form state behind the New and Schedule modes: agent,
// transport, working directory, title, model, effort, permission mode, and the
// CLI passthrough fields. Both modes drive identical inputs, so the state,
// derived capabilities, and reset effects live here and the panels only add
// their mode-specific fields and submit logic on top.
export interface LaunchForm {
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
  setTransport: Dispatch<SetStateAction<SessionTransport>>;
  permissionMode: string;
  setPermissionMode: (value: string) => void;
  customArgsText: string;
  setCustomArgsText: (value: string) => void;
  configOverridesText: string;
  setConfigOverridesText: (value: string) => void;
  launchEnvText: string;
  setLaunchEnvText: (value: string) => void;
  modelInfo: BackendModelListResponse | null;
  permissionOptions: ReturnType<typeof permissionModesFor>;
  supportsCustomArgs: boolean;
  supportsConfigOverrides: boolean;
  effortSupported: boolean;
  effortOptions: string[];
  changeBackend: (backend: Backend) => void;
  applyPreset: (spec: SessionPresetSpec) => void;
  handleModelsLoaded: (response: BackendModelListResponse) => void;
  collectArgs: () => {
    args: string[];
    configOverrides: string[];
    launchEnv: Record<string, string>;
  };
}

export function useLaunchForm({
  defaultBackend,
  defaultCwd,
  launchTargetId,
  defaultLaunchEnvByBackend,
  catalog,
}: UseLaunchFormParams): LaunchForm {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [title, setTitle] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [transport, setTransport] = useTransportForAgent(backend, catalog);
  const [permissionMode, setPermissionMode] = useState<string>("default");
  const [customArgsText, setCustomArgsText] = useState("");
  const [configOverridesText, setConfigOverridesText] = useState("");
  const [launchEnvText, setLaunchEnvText] = useState(() =>
    formatLaunchEnv(defaultLaunchEnvByBackend[defaultBackend]),
  );
  const [modelInfo, setModelInfo] = useState<BackendModelListResponse | null>(null);

  const permissionOptions = useMemo(
    () => permissionModesFor(backend, catalog),
    [backend, catalog],
  );

  const capabilities = catalog.byId(backend)?.capabilities;
  const supportsCustomArgs = capabilities?.supports_custom_cli_args ?? false;
  const supportsConfigOverrides = capabilities?.supports_config_overrides ?? false;
  // Codex's CLI has no `--effort` flag, so a tmux-wrapped codex session
  // can't honor an effort selection at launch time. Hide the picker
  // instead of letting the user pick a value that silently drops.
  const effortSupported = !(backend === "codex" && transport === "tmux");

  const changeBackend = useCallback((nextBackend: Backend) => {
    setBackend(nextBackend);
    setModel("");
    setEffort("");
    setModelInfo(null);
    setLaunchEnvText(formatLaunchEnv(defaultLaunchEnvByBackend[nextBackend]));
  }, [defaultLaunchEnvByBackend]);

  useEffect(() => {
    setBackend(defaultBackend);
    setModel("");
    setEffort("");
    setModelInfo(null);
    setLaunchEnvText(formatLaunchEnv(defaultLaunchEnvByBackend[defaultBackend]));
  }, [defaultBackend, defaultLaunchEnvByBackend]);

  useEffect(() => {
    setCwd(defaultCwd);
  }, [defaultCwd]);

  useEffect(() => {
    if (!permissionOptions.some((option) => option.id === permissionMode)) {
      setPermissionMode(permissionOptions[0]?.id ?? "default");
    }
  }, [permissionOptions, permissionMode]);

  // An "xhigh" effort carried over from one backend would be invalid
  // on the next, and per-backend model lists don't overlap.
  useEffect(() => {
    setEffort("");
    setModel("");
    setModelInfo(null);
    setLaunchEnvText(formatLaunchEnv(defaultLaunchEnvByBackend[backend]));
  }, [backend, launchTargetId, defaultLaunchEnvByBackend]);

  // Applying a preset that changes the backend triggers the reset effect above,
  // which clears model/effort/env (and useTransportForAgent resets transport).
  // So backend-scoped fields are stashed here and re-applied once, after those
  // resets settle, by the effect below. Same-backend applies run inline.
  const pendingPresetRef = useRef<(() => void) | null>(null);

  useEffect(() => {
    const pending = pendingPresetRef.current;
    if (pending) {
      pendingPresetRef.current = null;
      pending();
    }
  }, [backend]);

  const applyPreset = useCallback(
    (spec: SessionPresetSpec) => {
      const nextBackend = (spec.backend as Backend | null | undefined) ?? backend;
      // Fields untouched by the backend-reset can be set immediately.
      if (spec.cwd != null) setCwd(spec.cwd);
      if (spec.title != null) setTitle(spec.title);
      if (spec.permission_mode) setPermissionMode(spec.permission_mode);
      setCustomArgsText((spec.args ?? []).join("\n"));
      setConfigOverridesText((spec.config_overrides ?? []).join("\n"));
      // Backend-scoped fields: model/effort/env/transport are wiped by the
      // backend-change resets, so apply them after those run.
      const applyScoped = () => {
        setModel(spec.model ?? "");
        setEffort(spec.effort ?? "");
        setLaunchEnvText(formatLaunchEnv(spec.launch_env ?? {}));
        if (spec.transport) setTransport(spec.transport);
      };
      if (nextBackend !== backend) {
        pendingPresetRef.current = applyScoped;
        setBackend(nextBackend);
      } else {
        applyScoped();
      }
    },
    [backend, setTransport],
  );

  const effortOptions = useMemo(() => {
    if (!modelInfo) return [];

    const resolvedModelId = model || modelInfo.default_model_id;
    if (resolvedModelId) {
      const opt = modelInfo.models.find((entry) => entry.id === resolvedModelId);
      if (opt) {
        return opt.supported_efforts ?? [];
      }
    }

    // No explicit model picked and no default_model_id found — show the union
    // of every supported level so the picker still works against the backend's default model.
    const union = new Set<string>();
    for (const entry of modelInfo.models) {
      for (const level of entry.supported_efforts ?? []) {
        union.add(level);
      }
    }
    return Array.from(union);
  }, [modelInfo, model]);

  // Drop the picked level if the new model doesn't support it.
  useEffect(() => {
    if (effort && !effortOptions.includes(effort)) {
      setEffort("");
    }
  }, [effort, effortOptions]);

  const handleModelsLoaded = useCallback((response: BackendModelListResponse) => {
    setModelInfo(response);
  }, []);

  const collectArgs = useCallback(() => {
    const args = supportsCustomArgs
      ? customArgsText.split("\n").map((value) => value.trim()).filter(Boolean)
      : [];
    const configOverrides = supportsConfigOverrides
      ? configOverridesText.split("\n").map((value) => value.trim()).filter(Boolean)
      : [];
    const launchEnv = parseLaunchEnv(launchEnvText);
    return { args, configOverrides, launchEnv };
  }, [
    supportsCustomArgs,
    supportsConfigOverrides,
    customArgsText,
    configOverridesText,
    launchEnvText,
  ]);

  return {
    backend,
    cwd,
    setCwd,
    title,
    setTitle,
    model,
    setModel,
    effort,
    setEffort,
    transport,
    setTransport,
    permissionMode,
    setPermissionMode,
    customArgsText,
    setCustomArgsText,
    configOverridesText,
    setConfigOverridesText,
    launchEnvText,
    setLaunchEnvText,
    modelInfo,
    permissionOptions,
    supportsCustomArgs,
    supportsConfigOverrides,
    effortSupported,
    effortOptions,
    changeBackend,
    applyPreset,
    handleModelsLoaded,
    collectArgs,
  };
}

interface LaunchFormFieldsProps {
  form: LaunchForm;
  host: string;
  token: string;
  launchTargetId: string | null;
  targetLabel: string | null;
  recentCwds: string[];
  supportedBackends: Backend[];
  catalog: BackendCatalog;
  busy: boolean;
  onAuthFailure?: () => void;
  cwdError?: string | null;
  onClearCwdError?: () => void;
}

// The shared input block for New and Schedule: agent/transport picker, the
// working directory + title rows, the permission/model/effort row, and the
// collapsible Advanced section.
export function LaunchFormFields({
  form,
  host,
  token,
  launchTargetId,
  targetLabel,
  recentCwds,
  supportedBackends,
  catalog,
  busy,
  onAuthFailure,
  cwdError,
  onClearCwdError,
}: LaunchFormFieldsProps) {
  return (
    <>
      <AgentTransportPicker
        agents={supportedBackends}
        agent={form.backend}
        onAgentChange={form.changeBackend}
        transport={form.transport}
        onTransportChange={form.setTransport}
        catalog={catalog}
      />
      <div className="launch-body-grid">
        <WorkingDirectoryField
          cwd={form.cwd}
          onChange={form.setCwd}
          targetLabel={targetLabel}
          recentCwds={recentCwds}
          error={cwdError}
          onClearError={onClearCwdError}
        />
        <label className="field">
          <span>Title</span>
          <input
            value={form.title}
            onChange={(event) => form.setTitle(event.target.value)}
            placeholder="Optional"
          />
        </label>
        <div className="field-grid-row">
          {form.permissionOptions.length > 0 ? (
            <label className="field">
              <span>Permission mode</span>
              <select
                value={form.permissionMode}
                onChange={(event) => form.setPermissionMode(event.target.value)}
              >
                {form.permissionOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <ModelPicker
            key={`${form.backend}:${launchTargetId ?? "local"}`}
            host={host}
            token={token}
            backend={form.backend}
            launchTargetId={launchTargetId}
            value={form.model}
            onChange={form.setModel}
            onAuthFailure={onAuthFailure}
            onModelsLoaded={form.handleModelsLoaded}
            disabled={busy}
            defaultModelLabel={form.modelInfo?.default_model_label ?? null}
          />
          {form.effortSupported ? (
            <EffortPicker
              options={form.effortOptions}
              value={form.effort}
              onChange={form.setEffort}
              disabled={busy}
            />
          ) : null}
        </div>
      </div>
      <LaunchOptionsDetails
        mode="new"
        supportsCustomArgs={form.supportsCustomArgs}
        supportsConfigOverrides={form.supportsConfigOverrides}
        customArgsText={form.customArgsText}
        onCustomArgsChange={form.setCustomArgsText}
        configOverridesText={form.configOverridesText}
        onConfigOverridesChange={form.setConfigOverridesText}
        launchEnvText={form.launchEnvText}
        onLaunchEnvChange={form.setLaunchEnvText}
        formBusy={busy}
      />
    </>
  );
}
