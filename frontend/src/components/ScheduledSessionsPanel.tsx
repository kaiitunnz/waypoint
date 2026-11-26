"use client";

import { useState } from "react";

import type { BackendCatalog } from "@waypoint/web-core/backends";
import {
  humaniseBackend,
  permissionModeLabel,
} from "@waypoint/web-core/backends";
import { Pager } from "@/components/Pager";
import { usePagination } from "@waypoint/web-core/hooks/usePagination";
import { useScheduleModels } from "@waypoint/web-core/hooks/useScheduleModels";
import { formatClock, formatRelative } from "@waypoint/web-core/scheduleTime";
import {
  cronToLabel,
  formatInZone,
  isRecurring,
  timezoneAbbrev,
} from "@waypoint/web-core/recurrence";
import { ScheduledSession } from "@waypoint/web-core/types";

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
  const modelLabel = useScheduleModels(host, token, schedules);
  const [clearing, setClearing] = useState(false);

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
          modelName={modelLabel(schedule)}
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
  modelName,
}: {
  schedule: ScheduledSession;
  onCancel: (id: string) => Promise<void>;
  catalog: BackendCatalog;
  modelName: string | null;
}) {
  const when = new Date(schedule.scheduled_at);
  const recurring = isRecurring(schedule);
  const zone = schedule.timezone ?? undefined;
  const formatted = recurring
    ? `${formatInZone(when, zone)} ${timezoneAbbrev(when, zone)}`
    : formatClock(when);
  const relative = schedule.status === "pending" ? formatRelative(when) : null;
  const lastRun = schedule.last_run_at ? new Date(schedule.last_run_at) : null;
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
        {recurring ? (
          <span className="badge schedule-recurrence" title="Recurring schedule">
            {cronToLabel(schedule.cron, schedule.timezone)}
          </span>
        ) : null}
        {modeLabel ? (
          <span className="badge schedule-mode">{modeLabel}</span>
        ) : null}
        {schedule.model ? (
          <span className="badge model" title={`Model: ${schedule.model}`}>
            {modelName}
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
            {recurring ? <span className="schedule-when-label">Next</span> : null}
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
      {recurring && lastRun ? (
        <p className="schedule-last muted">
          <span className="schedule-when-label">Last</span>{" "}
          {formatInZone(lastRun, zone)} {timezoneAbbrev(lastRun, zone)}
          {schedule.last_run_status ? ` · ${schedule.last_run_status}` : ""}
        </p>
      ) : null}
      {recurring && schedule.last_failure_reason ? (
        <p className="error schedule-last-error">
          Last run failed: {schedule.last_failure_reason}
        </p>
      ) : null}
      {!recurring && schedule.failure_reason ? (
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
