"use client";

import {
  FormEvent,
  useCallback,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
} from "react";

import {
  LaunchFormFields,
  useLaunchForm,
} from "@/components/LaunchFormFields";
import { PresetSaveActions, PresetSelect } from "@/components/PresetBar";
import { ResumeThreadPanel } from "@/components/ResumeThreadPanel";
import type { BackendCatalog } from "@/lib/backends";
import { humaniseBackend } from "@/lib/backends";
import {
  AccountProfile,
  Backend,
  ScheduleCreateRequest,
  SessionPreset,
  SessionPresetSpec,
  SessionPresetSummary,
  SessionPresetWriteRequest,
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

type PanelMode = "new" | "resume" | "attach" | "schedule";

type ScheduleTiming = "delay" | "datetime";

interface LaunchPanelProps {
  host: string;
  token: string;
  defaultBackend: Backend;
  defaultCwd: string;
  defaultLaunchEnvByBackend: Record<Backend, Record<string, string>>;
  accountProfilesByBackend: Record<Backend, AccountProfile[]>;
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
    transport: SessionTransport | null,
    args: string[],
    configOverrides: string[],
    launchEnv: Record<string, string>,
    permissionMode: string | null,
    presetId: string | null,
    accountProfileId: string | null,
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
    transport: SessionTransport | null,
    importHistory: boolean,
    launchEnv: Record<string, string>,
  ) => Promise<void>;
  onDeleteThread?: (
    backend: Backend,
    threadId: string,
    launchTargetId?: string,
  ) => Promise<void>;
  onCreateSchedule: (payload: ScheduleCreateRequest) => Promise<void>;
  onAuthFailure?: () => void;
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
}

export function LaunchPanel({
  host,
  token,
  defaultBackend,
  defaultCwd,
  defaultLaunchEnvByBackend,
  accountProfilesByBackend,
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
}: LaunchPanelProps) {
  const [mode, setMode] = useState<PanelMode>("new");
  const form = useLaunchForm({
    defaultBackend,
    defaultCwd,
    defaultLaunchEnvByBackend,
    accountProfilesByBackend,
    launchTargetId,
    catalog,
  });
  const [selectedPresetId, setSelectedPresetId] = useState<string | null>(null);
  const presetHydratedRef = useRef(false);

  const applyPresetById = useCallback(
    async (id: string | null) => {
      setSelectedPresetId(id);
      if (!id) return;
      const summary = presets.find((preset) => preset.id === id);
      if (!summary) return;
      try {
        // A preset with env vars needs the full (unredacted) spec before hydrating.
        if ((summary.spec.launch_env_keys?.length ?? 0) > 0) {
          const full = await onFetchPresetSpec(id);
          form.applyPreset(full.spec);
        } else {
          // No env values to fetch; the redacted summary spec carries every
          // other field, and applyPreset ignores launch_env_keys.
          form.applyPreset(summary.spec as unknown as SessionPresetSpec);
        }
      } catch {
        // The full (env-carrying) spec couldn't load. Deselect rather than leave
        // a half-hydrated selection: an Update against it would otherwise
        // overwrite the preset's env with the form's empty env.
        setSelectedPresetId(null);
      }
    },
    [presets, onFetchPresetSpec, form],
  );

  // Hydrate the default preset into the shared form once on first load. Wait
  // for the id to actually arrive (bootstrap resolves after mount) before
  // consuming the once-guard, otherwise the first null run would disable it.
  useEffect(() => {
    if (presetHydratedRef.current) return;
    if (!defaultPresetId) return;
    presetHydratedRef.current = true;
    void applyPresetById(defaultPresetId);
  }, [defaultPresetId, applyPresetById]);
  const [tmuxTarget, setTmuxTarget] = useState("");
  const [prompt, setPrompt] = useState("");
  const [scheduleTiming, setScheduleTiming] = useState<ScheduleTiming>("delay");
  const [delayMinutes, setDelayMinutes] = useState("15");
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAt());
  const [scheduleError, setScheduleError] = useState("");
  const [formBusy, setFormBusy] = useState(false);

  async function submitCreate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      const { args, configOverrides, launchEnv } = form.collectArgs();
      await onCreate(
        form.backend,
        form.cwd,
        form.title,
        form.model.trim() || null,
        form.effortSupported ? form.effort.trim() || null : null,
        form.transport || null,
        args,
        configOverrides,
        launchEnv,
        form.permissionMode || null,
        selectedPresetId,
        form.accountProfileId || null,
      );
      form.setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitAttach(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFormBusy(true);
    try {
      await onAttach(tmuxTarget, form.backend, form.title);
      setTmuxTarget("");
      form.setTitle("");
    } finally {
      setFormBusy(false);
    }
  }

  async function submitSchedule(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setScheduleError("");
    const { args, configOverrides, launchEnv } = form.collectArgs();
    const payload: ScheduleCreateRequest = {
      backend: form.backend,
      cwd: form.cwd,
      // An explicit transport supersedes launch_mode at the API, so pin the
      // transport and leave the launch mode on "auto", matching the New panel.
      launch_mode: "auto",
      transport: form.transport || null,
      title: form.title.trim() || null,
      initial_prompt: prompt.trim() || null,
      permission_mode: form.permissionMode || null,
      model: form.model.trim() || null,
      effort: form.effortSupported ? form.effort.trim() || null : null,
      args,
      config_overrides: configOverrides,
      launch_env: launchEnv,
      // Record which preset seeded this schedule (provenance only; the fields
      // above are the resolved values the server persists).
      preset_id: selectedPresetId,
      account_profile_id: form.accountProfileId || null,
    };
    if (scheduleTiming === "delay") {
      const minutes = Number.parseFloat(delayMinutes);
      if (!Number.isFinite(minutes) || minutes < 0) {
        setScheduleError("Enter a non-negative delay in minutes.");
        return;
      }
      payload.delay_seconds = Math.round(minutes * 60);
    } else {
      const local = new Date(scheduledAt);
      if (Number.isNaN(local.getTime())) {
        setScheduleError("Enter a valid scheduled time.");
        return;
      }
      payload.scheduled_at = local.toISOString();
    }
    setFormBusy(true);
    try {
      await onCreateSchedule(payload);
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
            onAuthFailure={onAuthFailure}
            cwdError={cwdError}
            onClearCwdError={onClearCwdError}
          />
          <PresetSaveActions
            form={form}
            presets={presets}
            selectedPresetId={selectedPresetId}
            launchTargetId={launchTargetId}
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
            onAuthFailure={onAuthFailure}
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
          <div className="schedule-mode-row">
            <button
              type="button"
              className={scheduleTiming === "delay" ? "primary" : "secondary"}
              onClick={() => setScheduleTiming("delay")}
            >
              After delay
            </button>
            <button
              type="button"
              className={scheduleTiming === "datetime" ? "primary" : "secondary"}
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
          {scheduleError ? <p className="error">{scheduleError}</p> : null}
          <PresetSaveActions
            form={form}
            presets={presets}
            selectedPresetId={selectedPresetId}
            launchTargetId={launchTargetId}
            savePreset={onSavePreset}
            setDefaultPreset={onSetDefaultPreset}
            onSelectPreset={setSelectedPresetId}
          />
          <div className="launch-actions">
            <span className="grow" />
            <button className="primary" disabled={formBusy} type="submit">
              {formBusy ? "Scheduling…" : "Schedule"}
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
          preferredBackend={form.backend}
          onImportThread={onImportThread}
          onDeleteThread={onDeleteThread}
          catalog={catalog}
          defaultLaunchEnvByBackend={defaultLaunchEnvByBackend}
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

function defaultScheduledAt(): string {
  const date = new Date();
  date.setMinutes(date.getMinutes() + 15);
  // Format as YYYY-MM-DDTHH:mm for datetime-local input.
  const pad = (value: number) => value.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}
