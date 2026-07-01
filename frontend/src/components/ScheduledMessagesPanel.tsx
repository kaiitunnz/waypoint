"use client";

import { useEffect, useState } from "react";

import { ExpandableText } from "@/components/ExpandableText";
import { Pager } from "@/components/Pager";
import { usePagination } from "@/lib/usePagination";
import { formatClock, formatRelative } from "@/lib/scheduleTime";
import { MessageSchedule, SessionRecord } from "@/lib/types";

const PAGE_SIZE = 5;

interface ScheduledMessagesGroupProps {
  messageSchedules: MessageSchedule[];
  sessionsById?: Record<string, SessionRecord>;
  onDelete: (scheduleId: string) => Promise<void>;
  onClearHistory: () => Promise<void>;
}

// The "Messages" group inside the unified Scheduled panel: pending deliveries
// first (with live countdowns), then recently sent/failed/cancelled ones.
export function ScheduledMessagesGroup({
  messageSchedules,
  sessionsById,
  onDelete,
  onClearHistory,
}: ScheduledMessagesGroupProps) {
  // Re-render on a slow cadence so the "in 14m" countdowns stay honest without
  // hammering the main thread.
  const [, setTick] = useState(0);
  const [clearing, setClearing] = useState(false);

  const pending = messageSchedules.filter((ms) => ms.status === "pending");
  const recent = messageSchedules.filter((ms) => ms.status !== "pending");
  // Pending first (with live countdowns), then recent history, paginated as one
  // ordered list so the card stays a fixed height regardless of backlog.
  const ordered = [...pending, ...recent];
  const pager = usePagination(ordered, PAGE_SIZE);

  useEffect(() => {
    if (!pending.length) {
      return;
    }
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, [pending.length]);

  async function handleClearHistory() {
    if (clearing) {
      return;
    }
    setClearing(true);
    try {
      await onClearHistory();
    } finally {
      setClearing(false);
    }
  }

  if (!pending.length && !recent.length) {
    return null;
  }

  return (
    <div className="stack sched-group msg-sched">
      <div className="sched-group-head">
        <h4 className="sched-group-title">
          Messages
          {pending.length ? (
            <span className="msg-sched-count">{pending.length}</span>
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
      {pager.pageItems.map((ms) => (
        <MessageRow
          key={ms.id}
          schedule={ms}
          sessionTitle={sessionsById?.[ms.session_id]?.title ?? null}
          onDelete={onDelete}
        />
      ))}
      <Pager
        page={pager.page}
        totalPages={pager.totalPages}
        total={pager.total}
        pageStart={pager.pageStart}
        pageEnd={pager.pageEnd}
        onPage={pager.setPage}
        label="messages"
      />
    </div>
  );
}

function MessageRow({
  schedule,
  sessionTitle,
  onDelete,
}: {
  schedule: MessageSchedule;
  sessionTitle?: string | null;
  onDelete: (id: string) => Promise<void>;
}) {
  const when = schedule.scheduled_at
    ? new Date(schedule.scheduled_at)
    : schedule.created_at
      ? new Date(schedule.created_at)
      : null;
  const absolute = when ? formatClock(when) : "";
  const isPending = schedule.status === "pending";
  const relative = isPending && when ? formatRelative(when) : null;
  const label = sessionTitle?.trim() || shortSessionId(schedule.session_id);

  return (
    <article className={`schedule-row msg-row schedule-${schedule.status}`}>
      <div className="msg-row-top">
        <span className={`msg-status-dot ${schedule.status}`} aria-hidden="true" />
        <span className={`badge schedule-status ${schedule.status}`}>
          {schedule.status}
        </span>
        <a
          className="msg-session-link"
          href={`/session/${schedule.session_id}`}
          title={`Open session ${schedule.session_id}`}
        >
          <span className="msg-session-glyph" aria-hidden="true">
            ⇢
          </span>
          <span className="msg-session-name">{label}</span>
        </a>
        <span className="msg-row-right">
          <span className="msg-row-when">
            {relative ? <span className="msg-countdown">{relative}</span> : null}
            <span className="muted">{absolute}</span>
          </span>
          <button
            type="button"
            className="link-button danger-link msg-cancel action-chip"
            onClick={() => void onDelete(schedule.id)}
          >
            {isPending ? "Cancel" : "Dismiss"}
          </button>
        </span>
      </div>
      {schedule.text ? (
        <ExpandableText
          className="msg-text"
          text={schedule.text}
          collapsedMaxHeight="4.5em"
        />
      ) : (
        <p className="msg-text">
          <em className="muted">(no text)</em>
        </p>
      )}
      {schedule.failure_reason ? (
        <p className="error msg-error">{schedule.failure_reason}</p>
      ) : null}
    </article>
  );
}

function shortSessionId(id: string): string {
  // Session ids are "<backend>-<hex>"; the hex suffix is the unique part and
  // reads as an intentional handle, unlike a blind prefix truncation.
  const dash = id.lastIndexOf("-");
  return dash >= 0 ? id.slice(dash + 1) : id;
}

