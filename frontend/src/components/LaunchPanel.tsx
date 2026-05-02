"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { EffortPicker } from "@/components/EffortPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { ResumeThreadPanel } from "@/components/ResumeThreadPanel";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend } from "@/lib/backends";
import { Backend, BackendModelListResponse } from "@/lib/types";

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
  ) => Promise<void>;
  onAttach: (target: string, backendHint: Backend) => Promise<void>;
  onImportThread: (backend: Backend, threadId: string) => Promise<void>;
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
  const [modelInfo, setModelInfo] = useState<BackendModelListResponse | null>(null);
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  useEffect(() => {
    setBackend(defaultBackend);
  }, [defaultBackend]);

  // Reset effort whenever the backend changes — supported levels can shift
  // and an "xhigh" carried over from one backend would be invalid on the
  // next.
  useEffect(() => {
    setEffort("");
    setModelInfo(null);
  }, [backend, launchTargetId]);

  const effortOptions = useMemo(() => {
    if (!modelInfo) return [];
    if (model) {
      const opt = modelInfo.models.find((entry) => entry.id === model);
      return opt?.supported_efforts ?? [];
    }
    // No explicit model picked — show the union of every supported level so
    // the picker still works against the backend's default model.
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
      await onCreate(
        backend,
        cwd,
        title,
        model.trim() || null,
        effort.trim() || null,
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
          <select value={backend} onChange={(event) => setBackend(event.target.value as Backend)}>
            {supportedBackends.map((id) => (
              <option key={id} value={id}>
                {catalog.byId(id)?.label ?? humaniseBackend(id)}
              </option>
            ))}
          </select>
        </label>
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
          <select value={backend} onChange={(event) => setBackend(event.target.value as Backend)}>
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
