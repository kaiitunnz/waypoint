"use client";

import { MessageSchedule } from "@/lib/types";

interface ScheduledMessagesPanelProps {
  messageSchedules: MessageSchedule[];
  onDelete: (scheduleId: string) => Promise<void>;
  onClearHistory: () => Promise<void>;
}

export function ScheduledMessagesPanel({
  messageSchedules,
  onDelete,
  onClearHistory,
}: ScheduledMessagesPanelProps) {
  const pending = messageSchedules.filter((ms) => ms.status === "pending");
  const recent = messageSchedules.filter((ms) => ms.status !== "pending").slice(0, 4);

  if (!pending.length && !recent.length) {
    return null;
  }

  return (
    <section className="panel stack schedule-panel" aria-label="Scheduled messages">
      <div>
        <h3>Scheduled messages</h3>
        <p className="muted">Messages queued to send to sessions.</p>
      </div>
      {pending.length ? (
        <div className="stack">
          <h4 className="schedule-heading">Pending</h4>
          {pending.map((ms) => (
            <MessageRow key={ms.id} schedule={ms} onDelete={onDelete} />
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
              onClick={() => void onClearHistory()}
            >
              Clear history
            </button>
          </div>
          {recent.map((ms) => (
            <MessageRow key={ms.id} schedule={ms} onDelete={onDelete} />
          ))}
        </div>
      ) : null}
    </section>
  );
}

function MessageRow({
  schedule,
  onDelete,
}: {
  schedule: MessageSchedule;
  onDelete: (id: string) => Promise<void>;
}) {
  const when = schedule.scheduled_at
    ? new Date(schedule.scheduled_at)
    : schedule.created_at
      ? new Date(schedule.created_at)
      : null;
  const formatted = when ? when.toLocaleString() : "";
  return (
    <article className={`schedule-row schedule-${schedule.status}`}>
      <div className="session-row">
        <span className={`badge schedule-status ${schedule.status}`}>
          {schedule.status}
        </span>
        <span className="badge model" title={`Session: ${schedule.session_id}`}>
          {schedule.session_id.slice(0, 12)}…
        </span>
        <span className="muted">{formatted}</span>
      </div>
      <p className="schedule-title msg-text">{schedule.text}</p>
      {schedule.failure_reason ? (
        <p className="error">{schedule.failure_reason}</p>
      ) : null}
      <div className="action-row">
        <button
          type="button"
          className="link-button danger-link"
          onClick={() => void onDelete(schedule.id)}
        >
          {schedule.status === "pending" ? "Cancel" : "Dismiss"}
        </button>
      </div>
    </article>
  );
}
