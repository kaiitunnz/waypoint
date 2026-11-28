"use client";

import { useState } from "react";

import { useServerNow } from "@waypoint/web-core/hooks/useServerNow";
import type { BackgroundStatus, BackgroundTask, BackgroundTasks } from "@waypoint/web-core/session/backgroundTasks";
import { TASK_NOTIFICATION_BADGES } from "@waypoint/web-core/session/taskNotificationView";
import { formatRelativeTime } from "@waypoint/web-core/usage";

import { Disclosure } from "@/components/session/cards/Disclosure";
import { ToolGlyph } from "@/components/session/cards/ToolGlyph";

import { Section } from "./Section";
import styles from "./BackgroundSection.module.css";

// Rows shown before the rest fold behind "Show all".
const SHOWN = 3;

const STATUS_WORD: Record<BackgroundStatus, string> = {
  running: "Running",
  completed: "Completed",
  failed: "Failed",
  stopped: "Stopped",
};

export function backgroundCountText({ running, finished }: BackgroundTasks): string {
  if (running.length > 0) return `${running.length} running`;
  return finished.length > 0 ? `${finished.length} done` : "None";
}

function statusLine(task: BackgroundTask): string {
  const head =
    task.status === "running"
      ? `Running, started ${formatRelativeTime(task.startedAt)}`
      : `${STATUS_WORD[task.status]} ${formatRelativeTime(task.endedAt ?? task.startedAt)}`;
  return [head, ...task.usage, ...(task.status === "running" && task.lastEvent ? [task.lastEvent] : [])].join(", ");
}

// The tasks the agent started in the background and watches: commands, subagents, monitors.
export function BackgroundSection({ tasks }: { tasks: BackgroundTasks }) {
  // Ages move on.
  useServerNow(30_000);
  const [all, setAll] = useState(false);
  const rows = [...tasks.running, ...tasks.finished];
  const shown = all ? rows : rows.slice(0, SHOWN);
  return (
    <Section title="Background" count={backgroundCountText(tasks)}>
      <ul className={styles.tasks}>
        {shown.map((task) => {
          const badge = TASK_NOTIFICATION_BADGES[task.kind];
          return (
            <li key={task.id} className={styles.task}>
              <ToolGlyph glyph={badge.glyph} family={badge.family} className={styles.glyph} aria-hidden="true" />
              <span className={styles.label}>{task.label}</span>
              <span className={styles.status}>{statusLine(task)}</span>
            </li>
          );
        })}
      </ul>
      {rows.length > SHOWN ? (
        <div className={styles.more}>
          <Disclosure open={all} total={rows.length} noun="tasks" onToggle={() => setAll(!all)} />
        </div>
      ) : null}
    </Section>
  );
}
