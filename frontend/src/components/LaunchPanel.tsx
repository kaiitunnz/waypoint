"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { EffortPicker } from "@/components/EffortPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { ResumeThreadPanel } from "@/components/ResumeThreadPanel";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend } from "@/lib/backends";
import { Backend, BackendModelListResponse, LaunchMode } from "@/lib/types";

interface ThreadSummary {
  id: string;
  title: string;
  cwd: string;
  repo_name?: string | null;
  branch?: string | null;
  preview?: string | null;
  created_at: string;
  updated_at: string;
}

interface LaunchPanelProps {
  host: string;
  token: string;
  defaultBackend: Backend;
  defaultCwd: string;
  targetLabel: string | null;
  launchTargetId: string | null;
  recentCwds: string[];
  supportedBackends: Backend[];
  catalog: BackendCatalog;
  threadsByBackend: Record<Backend, ThreadSummary[]>;
  loadingByBackend: Record<Backend, boolean>;
  onCreate: (
    backend: Backend,
    cwd: string,
    title: string,
    model: string | null,
    effort: string | null,
    launchMode: LaunchMode,
    args: string[],
    configOverrides: string[],
  ) => Promise<void>;
  onAttach: (target: string, backendHint: Backend) => Promise<void>;
  onImportThread: (
    backend: Backend,
    threadId: string,
    cwd: string,
    launchMode: LaunchMode,
  ) => Promise<void>;
  onAuthFailure?: () => void;
}

export function LaunchPanel({
  host,
  token,
  defaultBackend,
  defaultCwd,
  targetLabel,
  launchTargetId,
  recentCwds,
  supportedBackends,
  catalog,
  threadsByBackend,
  loadingByBackend,
  onCreate,
  onAttach,
  onImportThread,
  onAuthFailure,
}: LaunchPanelProps) {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [title, setTitle] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [launchMode, setLaunchMode] = useState<LaunchMode>("auto");
  const [customArgsText, setCustomArgsText] = useState("");
  const [configOverridesText, setConfigOverridesText] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [modelInfo, setModelInfo] = useState<BackendModelListResponse | null>(null);
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  const capabilities = catalog.byId(backend)?.capabilities;
  const supportsCustomArgs = capabilities?.supports_custom_cli_args ?? false;
  const supportsConfigOverrides = capabilities?.supports_config_overrides ?? false;
  const showAdvancedSection = supportsCustomArgs || supportsConfigOverrides;

  const handleBackendChange = useCallback((nextBackend: Backend) => {
    setBackend(nextBackend);
    setModel("");
    setEffort("");
    setModelInfo(null);
  }, []);

  useEffect(() => {
    setBackend(defaultBackend);
    setModel("");
    setEffort("");
    setModelInfo(null);
  }, [defaultBackend]);

  // Reset effort and model whenever the backend changes — supported levels can shift
  // and an "xhigh" carried over from one backend would be invalid on the
  // next. Also, models are entirely different per backend.
  useEffect(() => {
    setEffort("");
    setModel("");
    setModelInfo(null);
  }, [backend, launchTargetId]);

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

  useEffect(() => {
    setCwd(defaultCwd);
  }, [defaultCwd]);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      const args = supportsCustomArgs
        ? customArgsText.split("\n").map((a) => a.trim()).filter(Boolean)
        : [];
      const configOverrides = supportsConfigOverrides
        ? configOverridesText.split("\n").map((a) => a.trim()).filter(Boolean)
        : [];
      await onCreate(
        backend,
        cwd,
        title,
        model.trim() || null,
        effort.trim() || null,
        launchMode,
        args,
        configOverrides,
      );
      setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      await onAttach(tmuxTarget, backend);
      setTmuxTarget("");
    } finally {
      setFormBusy(false);
    }
  }

  return (
    <section className="launch-grid">
      <form className="panel stack" onSubmit={submitCreate}>
        <div>
          <h3>New session</h3>
          <p className="muted">Launch through the wrapper for better transcript fidelity.</p>
        </div>
        <label className="field">
          <span>Backend</span>
          <select value={backend} onChange={(event) => handleBackendChange(event.target.value as Backend)}>
            {supportedBackends.map((id) => (
              <option key={id} value={id}>
                {catalog.byId(id)?.label ?? humaniseBackend(id)}
              </option>
            ))}
          </select>
        </label>
        <div className="field">
          <span>Launch mode</span>
          <div className="segmented segmented-quiet" role="radiogroup" aria-label="Launch mode">
            {[
              ["auto", "Auto"],
              ["direct", "Direct"],
              ["tmux_wrapper", "Via tmux wrapper"],
            ].map(([value, label]) => (
              <button
                key={value}
                type="button"
                role="radio"
                aria-checked={launchMode === value}
                className={`segmented-item ${launchMode === value ? "active" : ""}`}
                onClick={() => setLaunchMode(value as LaunchMode)}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
        <WorkingDirectoryField
          cwd={cwd}
          onChange={setCwd}
          targetLabel={targetLabel}
          recentCwds={recentCwds}
        />
        <label className="field">
          <span>Title</span>
          <input value={title} onChange={(event) => setTitle(event.target.value)} placeholder="Optional" />
        </label>
        <ModelPicker
          key={`${backend}:${launchTargetId ?? "local"}`}
          host={host}
          token={token}
          backend={backend}
          launchTargetId={launchTargetId}
          value={model}
          onChange={setModel}
          onAuthFailure={onAuthFailure}
          onModelsLoaded={handleModelsLoaded}
          disabled={formBusy}
          defaultModelLabel={modelInfo?.default_model_label ?? null}
        />
        <EffortPicker
          options={effortOptions}
          value={effort}
          onChange={setEffort}
          disabled={formBusy}
        />
        {showAdvancedSection ? (
          <div className={`advanced-section${showAdvanced ? " open" : ""}`}>
            <button
              type="button"
              className="advanced-toggle"
              onClick={() => setShowAdvanced((v) => !v)}
              aria-expanded={showAdvanced}
            >
              <svg className="advanced-toggle-gear" width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path d="M6 7.5a1.5 1.5 0 1 0 0-3 1.5 1.5 0 0 0 0 3Z" fill="currentColor" opacity="0.9"/>
                <path fillRule="evenodd" clipRule="evenodd" d="M4.95.75h2.1l.3 1.2a3.75 3.75 0 0 1 .87.5l1.17-.39.75 1.3-1 .77v.87l1 .76-.75 1.3-1.17-.39a3.75 3.75 0 0 1-.87.5l-.3 1.2H4.95l-.3-1.2a3.75 3.75 0 0 1-.87-.5l-1.17.39-.75-1.3 1-.76V5.1l-1-.77.75-1.3 1.17.39a3.75 3.75 0 0 1 .87-.5l.3-1.17ZM6 4.125A1.875 1.875 0 1 0 6 7.876 1.875 1.875 0 0 0 6 4.124Z" fill="currentColor" opacity="0.55"/>
              </svg>
              <span className="advanced-toggle-label">Advanced</span>
              <span className="advanced-toggle-chevron" aria-hidden="true" />
            </button>
            <div className="advanced-body">
              <div className="advanced-body-inner">
                {supportsCustomArgs ? (
                  <label className="field advanced-args-field">
                    <span>Custom CLI args</span>
                    <textarea
                      rows={3}
                      value={customArgsText}
                      onChange={(e) => setCustomArgsText(e.target.value)}
                      placeholder={"One flag per line, e.g.\n--dangerously-skip-permissions"}
                      disabled={formBusy}
                      spellCheck={false}
                      autoCapitalize="none"
                      autoComplete="off"
                      autoCorrect="off"
                    />
                  </label>
                ) : null}
                {supportsConfigOverrides ? (
                  <label className="field advanced-args-field">
                    <span>Config overrides (key=value)</span>
                    <textarea
                      rows={3}
                      value={configOverridesText}
                      onChange={(e) => setConfigOverridesText(e.target.value)}
                      placeholder={"One per line, e.g.\nmodel_reasoning_effort=\"high\""}
                      disabled={formBusy}
                      spellCheck={false}
                      autoCapitalize="none"
                      autoComplete="off"
                      autoCorrect="off"
                    />
                  </label>
                ) : null}
                <p className="advanced-warning">
                  Passed directly to the CLI binary — use with caution.
                </p>
              </div>
            </div>
          </div>
        ) : null}
        <button className="primary" disabled={formBusy} type="submit">
          Launch
        </button>
      </form>
      <form className="panel stack" onSubmit={submitAttach}>
        <div>
          <h3>Attach tmux</h3>
          <p className="muted">Observe an existing pane with raw terminal fallback.</p>
        </div>
        <label className="field">
          <span>Tmux target</span>
          <input value={tmuxTarget} onChange={(event) => setTmuxTarget(event.target.value)} placeholder="session:0.0" />
        </label>
        <label className="field">
          <span>Backend hint</span>
          <select value={backend} onChange={(event) => handleBackendChange(event.target.value as Backend)}>
            {supportedBackends.map((id) => (
              <option key={id} value={id}>
                {catalog.byId(id)?.label ?? humaniseBackend(id)}
              </option>
            ))}
          </select>
        </label>
        <button className="secondary" disabled={formBusy} type="submit">
          Attach
        </button>
      </form>
      {supportedBackends.length > 0 ? (
        <ResumeThreadPanel
          threadsByBackend={threadsByBackend}
          loadingByBackend={loadingByBackend}
          targetLabel={targetLabel}
          supportedBackends={supportedBackends}
          preferredBackend={backend}
          onImportThread={onImportThread}
          catalog={catalog}
        />
      ) : null}
    </section>
  );
}
