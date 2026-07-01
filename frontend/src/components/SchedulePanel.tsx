"use client";

import type { BackendCatalog } from "@/lib/backends";
import { MessageSchedule, ScheduledSession, SessionRecord } from "@/lib/types";
import { ScheduledMessagesGroup } from "@/components/ScheduledMessagesPanel";
import { ScheduledSessionsGroup } from "@/components/ScheduledSessionsPanel";

interface SchedulePanelProps {
  host: string;
  token: string;
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  sessions: SessionRecord[];
  catalog: BackendCatalog;
  onCancelSchedule: (scheduleId: string) => Promise<void>;
  onClearScheduleHistory: () => Promise<void>;
  onDeleteMessage: (scheduleId: string) => Promise<void>;
  onClearMessageHistory: () => Promise<void>;
}

// Single dashboard card for everything the scheduler will do: future session
// launches and messages queued to existing sessions. Each group renders itself
// and collapses when empty, so the card is hidden entirely when nothing is
// queued.
export function SchedulePanel({
  host,
  token,
  schedules,
  messageSchedules,
  sessions,
  catalog,
  onCancelSchedule,
  onClearScheduleHistory,
  onDeleteMessage,
  onClearMessageHistory,
}: SchedulePanelProps) {
  if (!schedules.length && !messageSchedules.length) {
    return null;
  }

  const sessionsById: Record<string, SessionRecord> = {};
  for (const session of sessions) {
    sessionsById[session.id] = session;
  }

  return (
    <section className="panel stack schedule-panel" aria-label="Scheduled">
      <div className="msg-sched-head">
        <h3>Scheduled</h3>
        <p className="muted">
          Upcoming session launches and messages queued to sessions.
        </p>
      </div>
      <ScheduledSessionsGroup
        host={host}
        token={token}
        schedules={schedules}
        catalog={catalog}
        onCancel={onCancelSchedule}
        onClearHistory={onClearScheduleHistory}
      />
      <ScheduledMessagesGroup
        messageSchedules={messageSchedules}
        sessionsById={sessionsById}
        onDelete={onDeleteMessage}
        onClearHistory={onClearMessageHistory}
      />
    </section>
  );
}
