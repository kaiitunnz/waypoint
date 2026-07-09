"use client";

import { useEffect, useState } from "react";

import type { BackendCatalog } from "@/lib/backends";
import {
  humaniseBackend,
  permissionModeLabel,
} from "@/lib/backends";
import { fetchBackendModels } from "@/lib/api";
import { Pager } from "@/components/Pager";
import { usePagination } from "@/lib/usePagination";
import { formatClock, formatRelative } from "@/lib/scheduleTime";
import {
  Backend,
  BackendModelOption,
  ScheduledSession,
} from "@/lib/types";

const PAGE_SIZE = 5;

interface ScheduledSessionsGroupProps {
  host: string;
  token: string;
  schedules: ScheduledSession[];
  catalog: BackendCatalog;
  onCancel: (scheduleId: string) => Promise<void>;
  onClearHistory: () => Promise<void>;
}

// The "Sessions" group inside the unified Scheduled panel: upcoming launches
// first, then recently-resolved schedules. The creation form lives under the
// Schedule tab of the launch panel; this is purely the management view and
// renders nothing until at least one schedule exists.
export function ScheduledSessionsGroup({
  host,
  token,
  schedules,
  catalog,
  onCancel,
  onClearHistory,
}: ScheduledSessionsGroupProps) {
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
  const recent = schedules.filter((schedule) => schedule.status !== "pending");
  const ordered = [...upcoming, ...recent];
  const pager = usePagination(ordered, PAGE_SIZE);

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
    <div className="stack sched-group">
      <div className="sched-group-head">
        <h4 className="sched-group-title">
          Sessions
          {upcoming.length ? (
            <span className="msg-sched-count">{upcoming.length}</span>
          ) : null}
        </h4>
        {recent.length ? (
          <button
            type="button"
            className="link-button danger-link"
            onClick={() => void handleClearHistory()}
            disabled={clearing}
          >
            {clearing ? "Clearing…" : "Clear history"}
          </button>
        ) : null}
      </div>
      {pager.pageItems.map((schedule) => (
        <ScheduleRow
          key={schedule.id}
          schedule={schedule}
          onCancel={onCancel}
          catalog={catalog}
          modelsByBackend={modelsByBackend}
        />
      ))}
      <Pager
        page={pager.page}
        totalPages={pager.totalPages}
        total={pager.total}
        pageStart={pager.pageStart}
        pageEnd={pager.pageEnd}
        onPage={pager.setPage}
        label="schedules"
      />
    </div>
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
  const formatted = formatClock(when);
  const relative = schedule.status === "pending" ? formatRelative(when) : null;
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
        {schedule.account_profile_label ? (
          <span
            className="badge account-profile"
            title={`Account profile: ${schedule.account_profile_label}`}
          >
            {schedule.account_profile_label}
          </span>
        ) : null}
        <span className="msg-row-right">
          <span className="msg-row-when">
            {relative ? <span className="msg-countdown">{relative}</span> : null}
            <span className="muted">{formatted}</span>
          </span>
          <button
            type="button"
            className="link-button danger-link action-chip"
            onClick={() => void onCancel(schedule.id)}
          >
            {schedule.status === "pending" ? "Cancel" : "Dismiss"}
          </button>
        </span>
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
      {schedule.session_id ? (
        <div className="action-row">
          <a className="link-button" href={`/session/${schedule.session_id}`}>
            Open session →
          </a>
        </div>
      ) : null}
    </article>
  );
}

function scheduleModelCacheKey(backend: Backend, launchTargetId: string | null): string {
  return `${backend}::${launchTargetId ?? "local"}`;
}
