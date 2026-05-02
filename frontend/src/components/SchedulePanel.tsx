"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { EffortPicker } from "@/components/EffortPicker";
import { ModelPicker } from "@/components/ModelPicker";
import { WorkingDirectoryField } from "@/components/WorkingDirectoryField";
import type { BackendCatalog } from "@/lib/backends";
import {
  humaniseBackend,
  permissionModeLabel,
  permissionModesFor,
} from "@/lib/backends";
import { fetchBackendModels } from "@/lib/api";
import {
  Backend,
  BackendModelListResponse,
  BackendModelOption,
  ScheduleCreateRequest,
  ScheduledSession,
} from "@/lib/types";

interface SchedulePanelProps {
  host: string;
  token: string;
  defaultBackend: Backend;
  defaultCwd: string;
  targetLabel: string | null;
  launchTargetId: string | null;
  recentCwds: string[];
  supportedBackends: Backend[];
  catalog: BackendCatalog;
  schedules: ScheduledSession[];
  onCreate: (payload: ScheduleCreateRequest) => Promise<void>;
  onCancel: (scheduleId: string) => Promise<void>;
  onClearHistory: () => Promise<void>;
  onAuthFailure?: () => void;
}

type Mode = "delay" | "datetime";

export function SchedulePanel({
  host,
  token,
  defaultBackend,
  defaultCwd,
  targetLabel,
  launchTargetId,
  recentCwds,
  supportedBackends,
  catalog,
  schedules,
  onCreate,
  onCancel,
  onClearHistory,
  onAuthFailure,
}: SchedulePanelProps) {
  const [backend, setBackend] = useState<Backend>(defaultBackend);
  const [cwd, setCwd] = useState(defaultCwd);
  const [title, setTitle] = useState("");
  const [prompt, setPrompt] = useState("");
  const [permissionMode, setPermissionMode] = useState<string>("default");
  const [model, setModel] = useState("");
  const [effort, setEffort] = useState("");
  const [modelInfo, setModelInfo] = useState<BackendModelListResponse | null>(null);
  const [modelsByBackend, setModelsByBackend] = useState<Record<string, BackendModelOption[]>>({});
  const [mode, setMode] = useState<Mode>("delay");
  const [delayMinutes, setDelayMinutes] = useState("15");
  const [scheduledAt, setScheduledAt] = useState(defaultScheduledAt());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const permissionOptions = useMemo(
    () => permissionModesFor(backend, catalog),
    [backend, catalog],
  );

  useEffect(() => setBackend(defaultBackend), [defaultBackend]);
  useEffect(() => setCwd(defaultCwd), [defaultCwd]);
  useEffect(() => {
    if (!permissionOptions.some((option) => option.id === permissionMode)) {
      setPermissionMode(permissionOptions[0]?.id ?? "default");
    }
  }, [permissionOptions, permissionMode]);
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
    const union = new Set<string>();
    for (const entry of modelInfo.models) {
      for (const level of entry.supported_efforts ?? []) {
        union.add(level);
      }
    }
    return Array.from(union);
  }, [modelInfo, model]);

  useEffect(() => {
    if (effort && !effortOptions.includes(effort)) {
      setEffort("");
    }
  }, [effort, effortOptions]);

  useEffect(() => {
    const modelCatalogs = new Map(
      schedules.map((schedule) => [
        scheduleModelCacheKey(schedule.backend, schedule.launch_target_id ?? null),
        {
          backend: schedule.backend,
          launchTargetId: schedule.launch_target_id ?? null,
        },
      ]),
    );
    for (const [cacheKey, target] of modelCatalogs) {
      if (!modelsByBackend[cacheKey]) {
        fetchBackendModels(host, token, target.backend, { launchTargetId: target.launchTargetId })
          .then((response) => {
            setModelsByBackend((prev) => ({ ...prev, [cacheKey]: response.models }));
          })
          .catch(() => {});
      }
    }
  }, [host, token, schedules, modelsByBackend]);

  const handleModelsLoaded = useCallback((response: BackendModelListResponse) => {
    setModelInfo(response);
  }, []);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError("");
    const payload: ScheduleCreateRequest = {
      backend,
      cwd,
      title: title.trim() || null,
      initial_prompt: prompt.trim() || null,
      permission_mode: permissionMode || null,
      model: model.trim() || null,
      effort: effort.trim() || null,
      args: [],
    };
    if (mode === "delay") {
      const minutes = Number.parseFloat(delayMinutes);
      if (!Number.isFinite(minutes) || minutes < 0) {
        setError("Enter a non-negative delay in minutes.");
        return;
      }
      payload.delay_seconds = Math.round(minutes * 60);
    } else {
      const local = new Date(scheduledAt);
      if (Number.isNaN(local.getTime())) {
        setError("Enter a valid scheduled time.");
        return;
      }
      payload.scheduled_at = local.toISOString();
    }
    setBusy(true);
    try {
      await onCreate(payload);
      setTitle("");
      setPrompt("");
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "schedule failed");
    } finally {
      setBusy(false);
    }
  }

  const upcoming = schedules.filter((schedule) => schedule.status === "pending");
  const recent = schedules.filter((schedule) => schedule.status !== "pending").slice(0, 4);
  const [clearing, setClearing] = useState(false);

  async function handleClearHistory() {
    if (clearing) {
      return;
    }
    if (!window.confirm(`Clear ${recent.length} non-pending schedule${recent.length === 1 ? "" : "s"}?`)) {
      return;
    }
    setClearing(true);
    try {
      await onClearHistory();
    } finally {
      setClearing(false);
    }
  }

  return (
    <section className="panel stack schedule-panel">
      <div>
        <h3>Schedule a session</h3>
        <p className="muted">
          Spin up a coding agent at a future time, optionally with an opening prompt.
        </p>
      </div>
      <form className="stack" onSubmit={submit}>
        <div className="schedule-grid">
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
          {permissionOptions.length > 0 ? (
            <label className="field">
              <span>Starting permission mode</span>
              <select
                value={permissionMode}
                onChange={(event) => setPermissionMode(event.target.value)}
              >
                {permissionOptions.map((option) => (
                  <option key={option.id} value={option.id}>
                    {option.label}
                  </option>
                ))}
              </select>
            </label>
          ) : null}
          <ModelPicker
            host={host}
            token={token}
            backend={backend}
            launchTargetId={launchTargetId}
            value={model}
            onChange={setModel}
            onAuthFailure={onAuthFailure}
            onModelsLoaded={handleModelsLoaded}
            disabled={busy}
            label="Model"
            defaultModelLabel={modelInfo?.default_model_label ?? null}
          />
          <EffortPicker
            options={effortOptions}
            value={effort}
            onChange={setEffort}
            disabled={busy}
          />
        </div>
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
            className={mode === "delay" ? "primary" : "secondary"}
            onClick={() => setMode("delay")}
          >
            After delay
          </button>
          <button
            type="button"
            className={mode === "datetime" ? "primary" : "secondary"}
            onClick={() => setMode("datetime")}
          >
            At specific time
          </button>
        </div>
        {mode === "delay" ? (
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
        {error ? <p className="error">{error}</p> : null}
        <button className="primary" disabled={busy} type="submit">
          {busy ? "Scheduling…" : "Schedule"}
        </button>
      </form>
      {upcoming.length ? (
        <div className="stack">
          <h4 className="schedule-heading">Upcoming</h4>
          {upcoming.map((schedule) => (
            <ScheduleRow
              key={schedule.id}
              schedule={schedule}
              onCancel={onCancel}
              catalog={catalog}
              modelsByBackend={modelsByBackend}
            />
          ))}
        </div>
      ) : null}
      {recent.length ? (
        <div className="stack">
          <div className="field-row">
            <h4 className="schedule-heading">Recent</h4>
            <button
              type="button"
              className="link-button danger-link"
              onClick={() => void handleClearHistory()}
              disabled={clearing}
            >
              {clearing ? "Clearing…" : "Clear history"}
            </button>
          </div>
          {recent.map((schedule) => (
            <ScheduleRow
              key={schedule.id}
              schedule={schedule}
              onCancel={onCancel}
              catalog={catalog}
              modelsByBackend={modelsByBackend}
            />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function ScheduleRow({
  schedule,
  onCancel,
  catalog,
  modelsByBackend,
}: {
  schedule: ScheduledSession;
  onCancel: (id: string) => Promise<void>;
  catalog: BackendCatalog;
  modelsByBackend: Record<string, BackendModelOption[]>;
}) {
  const when = new Date(schedule.scheduled_at);
  const formatted = when.toLocaleString();
  const relative = formatRelative(when);
  const modelCacheKey = scheduleModelCacheKey(
    schedule.backend,
    schedule.launch_target_id ?? null,
  );
  const modeLabel = permissionModeLabel(
    schedule.backend,
    schedule.permission_mode,
    catalog,
  );
  return (
    <article className={`schedule-row schedule-${schedule.status}`}>
      <div className="session-row">
        <span className={`badge ${schedule.backend}`}>
          {catalog.byId(schedule.backend)?.label ?? humaniseBackend(schedule.backend)}
        </span>
        <span className={`badge schedule-status ${schedule.status}`}>{schedule.status}</span>
        {modeLabel ? (
          <span className="badge schedule-mode">{modeLabel}</span>
        ) : null}
        {schedule.model ? (
          <span className="badge model" title={`Model: ${schedule.model}`}>
            {modelsByBackend[modelCacheKey]?.find((opt) => opt.id === schedule.model)?.label ?? schedule.model}
          </span>
        ) : null}
        {schedule.effort ? (
          <span className="badge effort" title={`Effort: ${schedule.effort}`}>
            {schedule.effort}
          </span>
        ) : null}
        <span className="muted">{formatted}</span>
        {schedule.status === "pending" ? <span className="muted">· {relative}</span> : null}
      </div>
      <p className="schedule-title">
        {schedule.title || schedule.cwd}
        {schedule.launch_target_id ? (
          <span className="muted"> · {schedule.launch_target_id}</span>
        ) : null}
      </p>
      {schedule.initial_prompt ? (
        <p className="muted schedule-prompt">“{schedule.initial_prompt}”</p>
      ) : null}
      {schedule.failure_reason ? (
        <p className="error">{schedule.failure_reason}</p>
      ) : null}
      <div className="action-row">
        {schedule.session_id ? (
          <a className="link-button" href={`/session/${schedule.session_id}`}>
            Open session →
          </a>
        ) : null}
        <button
          type="button"
          className="link-button danger-link"
          onClick={() => void onCancel(schedule.id)}
        >
          {schedule.status === "pending" ? "Cancel" : "Dismiss"}
        </button>
      </div>
    </article>
  );
}

function scheduleModelCacheKey(backend: Backend, launchTargetId: string | null): string {
  return `${backend}::${launchTargetId ?? "local"}`;
}

function formatRelative(target: Date): string {
  const diff = target.getTime() - Date.now();
  if (diff <= 0) {
    return "any moment";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 60) {
    return `in ${minutes}m`;
  }
  const hours = Math.round(minutes / 60);
  if (hours < 48) {
    return `in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `in ${days}d`;
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
