"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import { LaunchFormFields } from "@/components/LaunchFormFields";
import { PresetSaveActions, PresetSelect } from "@/components/PresetBar";
import { RecurrenceControl } from "@/components/RecurrenceControl";
import { ResumeThreadPanel } from "@/components/ResumeThreadPanel";
import { attachRequest, attachTargetError } from "@waypoint/web-core/launch/attach";
import { useLaunchForm } from "@waypoint/web-core/hooks/useLaunchForm";
import {
  loadPresetSpec,
  presetBackendUnsupported,
  type PresetSelection,
} from "@waypoint/web-core/launch/presets";
import { withScheduleTiming } from "@waypoint/web-core/launch/schedule";
import type { AgentThread, ThreadSummary } from "@waypoint/web-core/launch/threads";
import {
  defaultRecurrenceState,
  RecurrenceState,
  TimingMode,
} from "@waypoint/web-core/recurrence";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import { humaniseBackend } from "@waypoint/web-core/backends";
import {
  AccountProfile,
  Backend,
  ScheduleCreateRequest,
  SessionCreateRequest,
  SessionPreset,
  SessionPresetSummary,
  SessionPresetWriteRequest,
  UsageProviderOption,
} from "@waypoint/web-core/types";
import {
  defaultScheduledAt,
  type OneTimeTiming,
} from "@waypoint/web-core/session/scheduleMessage";

type PanelMode = "new" | "resume" | "attach" | "schedule";

interface LaunchPanelProps {
  host: string;
  token: string;
  defaultBackend: Backend;
  defaultCwd: string;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
  usageProviderOptions: UsageProviderOption[];
  targetLabel: string | null;
  launchTargetId: string | null;
  recentCwds: string[];
  supportedBackends: Backend[];
  catalog: BackendCatalog;
  threadsByBackend: Record<Backend, ThreadSummary[]>;
  loadingByBackend: Record<Backend, boolean>;
  // Reports the form's live (backend, accountProfileId) selection so the
  // caller can re-scope the resumable-thread fetch to the selected profile —
  // thread discovery is fetched app-globally, decoupled from this form.
  onDiscoveryScopeChange?: (backend: Backend, accountProfileId: string | null) => void;
  onCreate: (request: SessionCreateRequest) => Promise<void>;
  onAttach: (request: Record<string, unknown>) => Promise<void>;
  onImportThread: (thread: AgentThread, body: Record<string, unknown>) => Promise<void>;
  onDeleteThread?: (
    backend: Backend,
    threadId: string,
    launchTargetId?: string,
  ) => Promise<void>;
  onCreateSchedule: (payload: ScheduleCreateRequest) => Promise<void>;
  // Must be stable.
  onAuthFailure: () => void;
  // The cwd the backend rejected as nonexistent on the last New launch, or null.
  cwdError?: string | null;
  onClearCwdError?: () => void;
  presets: SessionPresetSummary[];
  defaultPresetId: string | null;
  onFetchPresetSpec: (presetId: string) => Promise<SessionPreset>;
  onSavePreset: (
    payload: SessionPresetWriteRequest,
    presetId: string | null,
  ) => Promise<string | null>;
  onSetDefaultPreset: (presetId: string) => Promise<void>;
  onDeletePreset: (presetId: string) => Promise<void>;
  // Optional controlled tab: when provided, the caller (e.g. the launch sheet)
  // owns which mode is shown; otherwise the panel tracks it internally.
  mode?: PanelMode;
  onModeChange?: (mode: PanelMode) => void;
  initialPreset?: PresetSelection;
}

export function LaunchPanel({
  host,
  token,
  defaultBackend,
  defaultCwd,
  defaultLaunchEnvByBackend,
  accountProfilesByBackend,
  usageProviderOptions,
  targetLabel,
  launchTargetId,
  recentCwds,
  supportedBackends,
  catalog,
  threadsByBackend,
  loadingByBackend,
  onDiscoveryScopeChange,
  onCreate,
  onAttach,
  onImportThread,
  onDeleteThread,
  onCreateSchedule,
  onAuthFailure,
  cwdError,
  onClearCwdError,
  presets,
  defaultPresetId,
  onFetchPresetSpec,
  onSavePreset,
  onSetDefaultPreset,
  onDeletePreset,
  mode: controlledMode,
  onModeChange,
  initialPreset,
}: LaunchPanelProps) {
  const [internalMode, setInternalMode] = useState<PanelMode>("new");
  const mode = controlledMode ?? internalMode;
  const setMode = onModeChange ?? setInternalMode;
  const form = useLaunchForm({
    host,
    token,
    onAuthFailure,
    fetchModels: mode === "new" || mode === "schedule",
    launchableBackends: supportedBackends,
    defaultBackend,
    defaultCwd,
    defaultLaunchEnvByBackend,
    accountProfilesByBackend,
    usageProviderOptions,
    launchTargetId,
    catalog,
  });

  useEffect(() => {
    onDiscoveryScopeChange?.(form.backend, form.accountProfileId || null);
  }, [form.backend, form.accountProfileId, onDiscoveryScopeChange]);

  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const presetHydratedRef = useRef(false);

  // A preset whose agent this target cannot launch stays selected under its
  // warning, but fills, sends and updates nothing.
  const selectedUnsupported =
    presetBackendUnsupported(presets.find((preset) => preset.id === selectedPresetId) ?? null, supportedBackends) !== null;
  const appliedPresetId = selectedUnsupported ? null : selectedPresetId;

  const applyPresetById = useCallback(
    async (id: string | null) => {
      setSelectedPresetId(id);
      if (!id) return;
      const summary = presets.find((preset) => preset.id === id);
      if (!summary || presetBackendUnsupported(summary, supportedBackends)) return;
      try {
        form.applyPreset(await loadPresetSpec(summary, onFetchPresetSpec));
      } catch {
        // The full (env-carrying) spec couldn't load. Deselect rather than leave
        // a half-hydrated selection: an Update against it would otherwise
        // overwrite the preset's env with the form's empty env.
        setSelectedPresetId(null);
      }
    },
    [presets, supportedBackends, onFetchPresetSpec, form],
  );

  // Seed the form's preset once per mount: a caller-requested preset wins,
  // otherwise the default. Wait for defaultPresetId to arrive (bootstrap
  // resolves after mount) before consuming the once-guard, or the first null
  // run disables it.
  useEffect(() => {
    if (presetHydratedRef.current) return;
    if (initialPreset?.kind === "preset") {
      presetHydratedRef.current = true;
      void applyPresetById(initialPreset.presetId);
      return;
    }
    if (!defaultPresetId) return;
    presetHydratedRef.current = true;
    void applyPresetById(defaultPresetId);
  }, [defaultPresetId, applyPresetById, initialPreset]);
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [prompt, setPrompt] = useState("");
  const [scheduleTiming, setScheduleTiming] = useState<OneTimeTiming>("delay");
  const [delayMinutes, setDelayMinutes] = useState("15");
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAt());
  const [scheduleError, setScheduleError] = useState("");
  const [timingMode, setTimingMode] = useState<TimingMode>("once");
  const [recurrence, setRecurrence] = useState<RecurrenceState>(
    defaultRecurrenceState,
  );
  const [recurrenceValid, setRecurrenceValid] = useState(false);
  const [formBusy, setFormBusy] = useState(false);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      await onCreate(form.createRequest(appliedPresetId));
      form.setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      await onAttach(attachRequest(tmuxTarget, form.backend, form.title));
      setTmuxTarget("");
      form.setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setScheduleError("");
    const built = withScheduleTiming(form.scheduleRequest(prompt, appliedPresetId), {
      timingMode,
      recurrence,
      timing: scheduleTiming,
      delay: delayMinutes,
      at: scheduledAt,
    });
    if ("error" in built) {
      setScheduleError(built.error);
      return;
    }
    setFormBusy(true);
    try {
      await onCreateSchedule(built.payload);
      form.setTitle("");
      setPrompt("");
    } catch (createError) {
      setScheduleError(createError instanceof Error ? createError.message : "schedule failed");
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
        <LaunchModeChooser
          mode={mode}
          onChange={(next) => {
            // The cwd field is shared across modes; a rejection from the New
            // form is stale once we leave it, so clear it on any mode switch.
            onClearCwdError?.();
            setMode(next);
          }}
        />
      </div>

      {mode === "new" ? (
        <form className="launch-body" onSubmit={submitCreate}>
          <PresetSelect
            presets={presets}
            selectedPresetId={selectedPresetId}
            onSelectPreset={(id) => void applyPresetById(id)}
            supportedBackends={supportedBackends}
            deletePreset={onDeletePreset}
          />
          <LaunchFormFields
            form={form}
            host={host}
            token={token}
            launchTargetId={launchTargetId}
            targetLabel={targetLabel}
            recentCwds={recentCwds}
            supportedBackends={supportedBackends}
            catalog={catalog}
            busy={formBusy}
            cwdError={cwdError}
            onClearCwdError={onClearCwdError}
          />
          <PresetSaveActions
            form={form}
            presets={presets}
            selectedPresetId={appliedPresetId}
            savePreset={onSavePreset}
            setDefaultPreset={onSetDefaultPreset}
            onSelectPreset={setSelectedPresetId}
          />
          <div className="launch-actions">
            <span className="grow" />
            <button className="primary" disabled={formBusy} type="submit">
              Launch session
            </button>
          </div>
        </form>
      ) : null}

      {mode === "schedule" ? (
        <form className="launch-body" onSubmit={submitSchedule}>
          <PresetSelect
            presets={presets}
            selectedPresetId={selectedPresetId}
            onSelectPreset={(id) => void applyPresetById(id)}
            supportedBackends={supportedBackends}
            deletePreset={onDeletePreset}
          />
          <LaunchFormFields
            form={form}
            host={host}
            token={token}
            launchTargetId={launchTargetId}
            targetLabel={targetLabel}
            recentCwds={recentCwds}
            supportedBackends={supportedBackends}
            catalog={catalog}
            busy={formBusy}
          />
          <label className="field">
            <span>Initial prompt</span>
            <textarea
              rows={3}
              value={prompt}
              onChange={(event) => setPrompt(event.target.value)}
              placeholder="Optional — sent automatically once the session starts"
            />
          </label>
          <RecurrenceControl
            host={host}
            token={token}
            timing={timingMode}
            onTimingChange={setTimingMode}
            state={recurrence}
            onStateChange={setRecurrence}
            onValidChange={setRecurrenceValid}
          >
            <div className="segmented" role="tablist" aria-label="One-time timing">
              <button
                type="button"
                role="tab"
                aria-selected={scheduleTiming === "delay"}
                className={`segmented-item${
                  scheduleTiming === "delay" ? " active" : ""
                }`}
                onClick={() => setScheduleTiming("delay")}
              >
                After delay
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={scheduleTiming === "datetime"}
                className={`segmented-item${
                  scheduleTiming === "datetime" ? " active" : ""
                }`}
                onClick={() => setScheduleTiming("datetime")}
              >
                At specific time
              </button>
            </div>
            {scheduleTiming === "delay" ? (
              <label className="field">
                <span>Minutes from now</span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  value={delayMinutes}
                  onChange={(event) => setDelayMinutes(event.target.value)}
                />
              </label>
            ) : (
              <label className="field">
                <span>Local time</span>
                <input
                  type="datetime-local"
                  value={scheduledAt}
                  onChange={(event) => setScheduledAt(event.target.value)}
                />
              </label>
            )}
          </RecurrenceControl>
          {scheduleError ? <p className="error">{scheduleError}</p> : null}
          <PresetSaveActions
            form={form}
            presets={presets}
            selectedPresetId={appliedPresetId}
            savePreset={onSavePreset}
            setDefaultPreset={onSetDefaultPreset}
            onSelectPreset={setSelectedPresetId}
          />
          <div className="launch-actions">
            <span className="grow" />
            <button
              className="primary"
              disabled={formBusy || (timingMode === "repeat" && !recurrenceValid)}
              type="submit"
            >
              {formBusy ? "Scheduling…" : "Schedule"}
            </button>
          </div>
        </form>
      ) : null}

      {mode === "resume" && supportedBackends.length > 0 ? (
        <ResumeThreadPanel
          host={host}
          token={token}
          launchTargetId={launchTargetId}
          accountProfileId={form.accountProfileId || null}
          threadsByBackend={threadsByBackend}
          loadingByBackend={loadingByBackend}
          targetLabel={targetLabel}
          supportedBackends={supportedBackends}
          preferredBackend={form.backend}
          onImportThread={onImportThread}
          onDeleteThread={onDeleteThread}
          catalog={catalog}
          defaultLaunchEnvByBackend={defaultLaunchEnvByBackend}
          onAuthFailure={onAuthFailure}
        />
      ) : null}

      {mode === "attach" ? (
        <form className="launch-body" onSubmit={submitAttach}>
          <div className="launch-body-col">
            <label className="field">
              <span>Terminal pane</span>
              <input
                value={tmuxTarget}
                onChange={(event) => setTmuxTarget(event.target.value)}
                placeholder="session:window.pane — e.g. main:0.0"
              />
            </label>
            <label className="field">
              <span>Agent hint</span>
              <select value={form.backend} onChange={(event) => form.changeBackend(event.target.value as Backend)}>
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
                value={form.title}
                onChange={(event) => form.setTitle(event.target.value)}
                placeholder="Optional"
              />
            </label>
          </div>
          <div className="launch-actions">
            <span className="grow muted">Attaches over the Terminal interface — Waypoint streams the live pane and forwards your keystrokes.</span>
            <button
              className="primary"
              disabled={formBusy || attachTargetError(tmuxTarget) !== null}
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
  ["schedule", "Schedule"],
];

// Animated segmented control: a sliding pill backdrop tracks the active
// button by measuring its offsetLeft/offsetWidth in a layout effect, then
// setting CSS variables on the parent. The transition between positions
// is a smooth spring curve, so flipping between modes feels alive rather
// than the old hard color flip.
function LaunchModeChooser({ mode, onChange }: LaunchModeChooserProps) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const buttonRefs = useRef<Record<PanelMode, HTMLButtonElement | null>>({
    new: null,
    resume: null,
    attach: null,
    schedule: null,
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
      return "Attach to a running terminal pane and drive it live.";
    case "schedule":
      return `Spin up an agent${where} at a future time, optionally with an opening prompt.`;
  }
}
