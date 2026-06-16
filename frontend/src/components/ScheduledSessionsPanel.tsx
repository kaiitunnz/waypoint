"use client";

import { useEffect, useState } from "react";

import type { BackendCatalog } from "@/lib/backends";
import {
  humaniseBackend,
  permissionModeLabel,
} from "@/lib/backends";
import { fetchBackendModels } from "@/lib/api";
import {
  Backend,
  BackendModelOption,
  ScheduledSession,
} from "@/lib/types";

interface ScheduledSessionsPanelProps {
  host: string;
  token: string;
  schedules: ScheduledSession[];
  catalog: BackendCatalog;
  onCancel: (scheduleId: string) => Promise<void>;
  onClearHistory: () => Promise<void>;
}

// The list of upcoming and recent scheduled sessions. The creation form now
// lives under the Schedule tab of the launch panel; this panel is purely the
// management view and renders nothing until at least one schedule exists.
export function ScheduledSessionsPanel({
  host,
  token,
  schedules,
  catalog,
  onCancel,
  onClearHistory,
}: ScheduledSessionsPanelProps) {
  const [modelsByBackend, setModelsByBackend] = useState<Record<string, BackendModelOption[]>>({});
  const [clearing, setClearing] = useState(false);

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

  const upcoming = schedules.filter((schedule) => schedule.status === "pending");
  const recent = schedules.filter((schedule) => schedule.status !== "pending").slice(0, 4);

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

  if (!upcoming.length && !recent.length) {
    return null;
  }

  return (
    <section className="panel stack schedule-panel" aria-label="Scheduled sessions">
      <div>
        <h3>Scheduled sessions</h3>
        <p className="muted">Sessions waiting to start, and recently completed schedules.</p>
      </div>
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
