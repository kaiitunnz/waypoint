"use client";

import { useEffect, useState } from "react";

import { TodoProgress } from "@/lib/todos";
import { TodoListBody } from "@/components/TodoList";

// A persistent, glanceable readout of the session's current task group,
// docked above the composer so live progress stays visible as the transcript
// scrolls. Collapsed it shows the current task + a progress meter; tapping it
// expands the full list (a bottom sheet on mobile, a popover on desktop).
export function TaskProgressDock({
  progress,
  onDismiss,
}: {
  progress: TodoProgress;
  onDismiss: () => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { todos, total, completed, current, allComplete } = progress;
  const pct = total > 0 ? Math.round((completed / total) * 100) : 0;

  useEffect(() => {
    if (!expanded) {
      return;
    }
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setExpanded(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  const glyph = allComplete ? "✓" : current?.status === "in-progress" ? "◐" : "○";
  const label = allComplete
    ? "All tasks complete"
    : current
      ? current.text
      : "Tasks";

  return (
    <div className={`task-dock ${allComplete ? "complete" : "active"}`}>
      {expanded ? (
        <>
          <button
            type="button"
            className="task-dock-scrim"
            aria-label="Close task list"
            onClick={() => setExpanded(false)}
          />
          <div className="task-dock-panel" role="dialog" aria-label="Task list">
            <div className="task-dock-panel-head">
              <span className="task-dock-panel-title">Tasks</span>
              <span className="task-dock-count">
                {completed}/{total}
              </span>
              <button
                type="button"
                className="task-dock-collapse"
                aria-label="Collapse task list"
                onClick={() => setExpanded(false)}
              >
                ⌄
              </button>
            </div>
            <TodoListBody todos={todos} />
          </div>
        </>
      ) : null}
      <div className="task-dock-strip">
        <div
          className="task-dock-meter"
          role="progressbar"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={completed}
          aria-label={`${completed} of ${total} tasks complete`}
        >
          <div className="task-dock-meter-fill" style={{ width: `${pct}%` }} />
        </div>
        <div className="task-dock-row">
          <button
            type="button"
            className="task-dock-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            <span className="task-dock-glyph" aria-hidden>
              {glyph}
            </span>
            <span className="task-dock-label">{label}</span>
            <span className="task-dock-count">
              {completed}/{total}
            </span>
            <span className="task-dock-chevron" aria-hidden>
              {expanded ? "⌄" : "⌃"}
            </span>
          </button>
          <button
            type="button"
            className="task-dock-dismiss"
            aria-label="Dismiss task progress"
            onClick={onDismiss}
          >
            ×
          </button>
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {label}
      </span>
    </div>
  );
}
