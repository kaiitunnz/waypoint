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

import { useTransportForAgent } from "@/components/AgentTransportPicker";
import { EffortPicker } from "@/components/EffortPicker";
import {
  formatLaunchEnv,
  LaunchOptionsDetails,
  parseLaunchEnv,
} from "@/components/LaunchOptions";
import { ModelPicker } from "@/components/ModelPicker";
import { SessionContextFields } from "@/components/SessionContextFields";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import { permissionModesFor } from "@/lib/backends";
import {
  AccountProfile,
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
  // Account/config profiles keyed by backend — target-merged when a launch
  // target is active, else the global per-backend catalogue.
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
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
  accountProfileId: string;
  setAccountProfileId: (value: string) => void;
  accountProfiles: AccountProfile[];
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
  accountProfilesByBackend,
  catalog,
}: UseLaunchFormParams): LaunchForm {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [title, setTitle] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [transport, setTransport] = useTransportForAgent(backend, catalog);
  const [permissionMode, setPermissionMode] = useState<string>("default");
  const [accountProfileId, setAccountProfileId] = useState("");
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

  const accountProfiles = useMemo(
    () => accountProfilesByBackend[backend] ?? [],
    [accountProfilesByBackend, backend],
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
    setAccountProfileId("");
    setLaunchEnvText(formatLaunchEnv(defaultLaunchEnvByBackend[nextBackend]));
  }, [defaultLaunchEnvByBackend]);

  useEffect(() => {
    setBackend(defaultBackend);
    setModel("");
    setEffort("");
    setModelInfo(null);
    setAccountProfileId("");
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
    setAccountProfileId("");
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
      // Presets carry launch defaults only — cwd and title are per-launch and
      // deliberately left as the user set them.
      if (spec.permission_mode) setPermissionMode(spec.permission_mode);
      setCustomArgsText((spec.args ?? []).join("\n"));
      setConfigOverridesText((spec.config_overrides ?? []).join("\n"));
      // Backend-scoped fields: model/effort/env/transport are wiped by the
      // backend-change resets, so apply them after those run.
      const applyScoped = () => {
        setModel(spec.model ?? "");
        setEffort(spec.effort ?? "");
        setAccountProfileId(spec.account_profile_id ?? "");
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

  // Drop the picked level if the loaded model doesn't support it. Gated on
  // modelInfo: while models are still loading (modelInfo === null) the option
  // list is empty and we can't yet tell a valid level from an invalid one —
  // clamping then would wipe a preset-applied effort before its model arrives.
  useEffect(() => {
    if (modelInfo && effort && !effortOptions.includes(effort)) {
      setEffort("");
    }
  }, [effort, effortOptions, modelInfo]);

  // Drop a selected profile the current backend/target doesn't offer (e.g. a
  // preset carrying a profile valid elsewhere) so the launch never submits an
  // id the server would reject; falls back to the service-default option.
  useEffect(() => {
    if (
      accountProfileId &&
      !accountProfiles.some((profile) => profile.id === accountProfileId)
    ) {
      setAccountProfileId("");
    }
  }, [accountProfileId, accountProfiles]);

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
    accountProfileId,
    setAccountProfileId,
    accountProfiles,
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

// The shared input block for New and Schedule, ordered by the settings
// hierarchy: session context (agent/account profile/interface) selects the
// agent namespace and config root, then session subject (cwd/title), then
// runtime tuning (permission/model/effort) constrained by that context, then
// the collapsible Advanced section. Mono section captions group the fields as
// a flat instrument panel — not nested cards.
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
    <div className="launch-sections">
      <section className="launch-section">
        <span className="launch-section-caption">Session context</span>
        <SessionContextFields
          agents={supportedBackends}
          agent={form.backend}
          onAgentChange={form.changeBackend}
          transport={form.transport}
          onTransportChange={form.setTransport}
          accountProfiles={form.accountProfiles}
          accountProfileId={form.accountProfileId}
          onAccountProfileChange={form.setAccountProfileId}
          targetLabel={targetLabel}
          catalog={catalog}
        />
      </section>

      <section className="launch-section">
        <span className="launch-section-caption">Session</span>
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
      </section>

      <section className="launch-section">
        <span className="launch-section-caption">Tuning</span>
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
      </section>

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
    </div>
  );
}
