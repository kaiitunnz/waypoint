"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from "react";

import { EffortPicker } from "@/components/EffortPicker";
import { LaunchOptionsDetails } from "@/components/LaunchOptions";
import { ModelPicker } from "@/components/ModelPicker";
import { ResumeThreadPanel } from "@/components/ResumeThreadPanel";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend, launchModesFor } from "@/lib/backends";
import {
  Backend,
  BackendModelListResponse,
  LaunchMode,
  SessionTransport,
} from "@/lib/types";

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

type PanelMode = "new" | "resume" | "attach";

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
    transport: SessionTransport | null,
    args: string[],
    configOverrides: string[],
  ) => Promise<void>;
  onAttach: (
    target: string,
    backendHint: Backend,
    title: string,
  ) => Promise<void>;
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
  const [mode, setMode] = useState<PanelMode>("new");
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [title, setTitle] = useState("");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [launchMode, setLaunchMode] = useState<LaunchMode>("auto");
  const [customArgsText, setCustomArgsText] = useState("");
  const [configOverridesText, setConfigOverridesText] = useState("");
  const [modelInfo, setModelInfo] = useState<BackendModelListResponse | null>(null);
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  const capabilities = catalog.byId(backend)?.capabilities;
  const supportsCustomArgs = capabilities?.supports_custom_cli_args ?? false;
  const supportsConfigOverrides = capabilities?.supports_config_overrides ?? false;
  // The transport/fidelity options available for the selected agent. "direct"
  // is the native structured adapter, "tmux_wrapper" the generic terminal
  // pane, "auto" lets the backend choose.
  const availableLaunchModes = useMemo(
    () => launchModesFor(backend, catalog),
    [backend, catalog],
  );
  // Codex's CLI has no `--effort` flag, so a tmux-wrapped codex session
  // can't honor an effort selection at launch time. Hide the picker
  // instead of letting the user pick a value that silently drops.
  const effortSupported = !(backend === "codex" && launchMode === "tmux_wrapper");

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

  // An "xhigh" effort carried over from one backend would be invalid
  // on the next, and per-backend model lists don't overlap.
  useEffect(() => {
    setEffort("");
    setModel("");
    setModelInfo(null);
  }, [backend, launchTargetId]);

  // Fall back to "auto" when the chosen transport isn't offered by the agent.
  useEffect(() => {
    setLaunchMode((mode) =>
      availableLaunchModes.includes(mode) ? mode : "auto",
    );
  }, [availableLaunchModes]);

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
        effortSupported ? effort.trim() || null : null,
        launchMode,
        null,
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
      await onAttach(tmuxTarget, backend, title);
      setTmuxTarget("");
      setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  const subhead = subheadFor(mode, targetLabel);

  return (
    <section className="launch-card" aria-label="Start a session">
      <div className="launch-card-head">
        <div className="launch-card-titles">
          <h3>Start a session</h3>
          <p className="muted">{subhead}</p>
        </div>
        <LaunchModeChooser mode={mode} onChange={setMode} />
      </div>

      {mode === "new" ? (
        <form className="launch-body" onSubmit={submitCreate}>
          <div className="launch-body-grid two-col">
            <div className="launch-body-col">
              {/* The Backend select lists the launchable agents (the catalog
                  minus the tmux managed-launch fallback); the transport is
                  chosen separately in Advanced. claude_tty stays its own agent
                  entry rather than a claude_code transport option because no
                  launch-mode selects the tty-tail transport yet.
                  TODO: collapse to a single agent-primary picker (agent +
                  transport sub-select) once the backend wires a launch-mode
                  for the tty-tail transport. */}
              <label className="field">
                <span>Agent</span>
                <select value={backend} onChange={(event) => handleBackendChange(event.target.value as Backend)}>
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
            </div>
            <div className="launch-body-col">
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
              {effortSupported ? (
                <EffortPicker
                  options={effortOptions}
                  value={effort}
                  onChange={setEffort}
                  disabled={formBusy}
                />
              ) : null}
            </div>
          </div>
          <LaunchOptionsDetails
            mode="new"
            launchMode={launchMode}
            onLaunchModeChange={setLaunchMode}
            availableModes={availableLaunchModes}
            supportsCustomArgs={supportsCustomArgs}
            supportsConfigOverrides={supportsConfigOverrides}
            customArgsText={customArgsText}
            onCustomArgsChange={setCustomArgsText}
            configOverridesText={configOverridesText}
            onConfigOverridesChange={setConfigOverridesText}
            formBusy={formBusy}
          />
          <div className="launch-actions">
            <span className="grow muted">Auto picks the structured adapter when available for better transcript fidelity.</span>
            <button className="primary" disabled={formBusy} type="submit">
              Launch session
            </button>
          </div>
        </form>
      ) : null}

      {mode === "resume" && supportedBackends.length > 0 ? (
        <ResumeThreadPanel
          threadsByBackend={threadsByBackend}
          loadingByBackend={loadingByBackend}
          targetLabel={targetLabel}
          supportedBackends={supportedBackends}
          preferredBackend={backend}
          onImportThread={onImportThread}
          catalog={catalog}
          launchMode={launchMode}
          onLaunchModeChange={setLaunchMode}
        />
      ) : null}

      {mode === "attach" ? (
        <form className="launch-body" onSubmit={submitAttach}>
          <div className="launch-body-col">
            <label className="field">
              <span>Tmux target</span>
              <input
                value={tmuxTarget}
                onChange={(event) => setTmuxTarget(event.target.value)}
                placeholder="session:window.pane — e.g. main:0.0"
              />
            </label>
            <label className="field">
              <span>Agent hint</span>
              <select value={backend} onChange={(event) => handleBackendChange(event.target.value as Backend)}>
                {supportedBackends.map((id) => (
                  <option key={id} value={id}>
                    {catalog.byId(id)?.label ?? humaniseBackend(id)}
                  </option>
                ))}
              </select>
            </label>
            <label className="field">
              <span>Title</span>
              <input
                value={title}
                onChange={(event) => setTitle(event.target.value)}
                placeholder="Optional"
              />
            </label>
          </div>
          <div className="launch-actions">
            <span className="grow muted">Waypoint will pipe the pane and forward keystrokes through xterm.</span>
            <button
              className="primary"
              disabled={formBusy || !tmuxTarget.trim()}
              type="submit"
            >
              Attach
            </button>
          </div>
        </form>
      ) : null}
    </section>
  );
}

interface LaunchModeChooserProps {
  mode: PanelMode;
  onChange: (mode: PanelMode) => void;
}

const MODE_OPTIONS: Array<[PanelMode, string]> = [
  ["new", "New"],
  ["resume", "Resume"],
  ["attach", "Attach"],
];

// Animated segmented control: a sliding pill backdrop tracks the active
// button by measuring its offsetLeft/offsetWidth in a layout effect, then
// setting CSS variables on the parent. The transition between positions
// is a smooth spring curve, so flipping between New / Resume / Attach
// feels alive rather than the old hard color flip.
function LaunchModeChooser({ mode, onChange }: LaunchModeChooserProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const buttonRefs = useRef<Record<PanelMode, HTMLButtonElement | null>>({
    new: null,
    resume: null,
    attach: null,
  });

  // ResizeObserver catches width changes from window resize, label
  // content swaps, AND late-arriving web font swaps — the latter
  // would otherwise leave the pill at the system-font measurement
  // until the next mode click.
  useLayoutEffect(() => {
    const wrap = wrapRef.current;
    const button = buttonRefs.current[mode];
    if (!wrap || !button) return;
    const sync = () => {
      wrap.style.setProperty("--lm-left", `${button.offsetLeft}px`);
      wrap.style.setProperty("--lm-width", `${button.offsetWidth}px`);
    };
    sync();
    const ro = new ResizeObserver(sync);
    ro.observe(button);
    return () => ro.disconnect();
  }, [mode]);

  return (
    <div
      ref={wrapRef}
      className="launch-mode"
      role="tablist"
      aria-label="Launch mode"
    >
      {MODE_OPTIONS.map(([value, label]) => (
        <button
          key={value}
          ref={(node) => {
            buttonRefs.current[value] = node;
          }}
          type="button"
          role="tab"
          aria-selected={mode === value}
          className={mode === value ? "active" : ""}
          onClick={() => onChange(value)}
        >
          {label}
        </button>
      ))}
    </div>
  );
}

function subheadFor(mode: PanelMode, targetLabel: string | null): string {
  const where = targetLabel ? ` on ${targetLabel}` : "";
  switch (mode) {
    case "new":
      return `Spin up a new agent${where}.`;
    case "resume":
      return `Pick up a stored thread${where}.`;
    case "attach":
      return "Observe an existing tmux pane with raw terminal fallback.";
  }
}
