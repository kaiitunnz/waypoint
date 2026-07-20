"use client";

import { useEffect, useState } from "react";

import { ExpandableText } from "@/components/ExpandableText";
import { cronToLabel, isRecurring } from "@/lib/recurrence";
import { MessageSchedule } from "@/lib/types";

// A single chevron glyph (pointing up); orientation handled with a CSS rotation,
// matching the task-progress and side-question docks so the three read as one
// family.
function ChevronIcon() {
  return (
    <svg
      viewBox="0 0 16 16"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
    >
      <path d="M4 10l4-4 4 4" />
    </svg>
  );
}

interface ScheduledMessagesDockProps {
  messages: MessageSchedule[];
  onCancel: (scheduleId: string) => Promise<void> | void;
}

// A persistent, glanceable readout of this session's pending scheduled
// messages, docked above the composer so it stays visible as the transcript
// scrolls. Collapsed it shows the next delivery + countdown; tapping it expands
// the full list (a bottom sheet on mobile, a popover on desktop). Mirrors the
// TaskProgressDock structure and surface.
export function ScheduledMessagesDock({
  messages,
  onCancel,
}: ScheduledMessagesDockProps) {
  const [expanded, setExpanded] = useState(false);
  const [, setTick] = useState(0);

  useEffect(() => {
    if (!messages.length) {
      return;
    }
    const id = window.setInterval(() => setTick((t) => t + 1), 30_000);
    return () => window.clearInterval(id);
  }, [messages.length]);

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

  if (!messages.length) {
    return null;
  }

  const next = messages[0];
  const label = next.text || "(no text)";

  return (
    <div className={`sched-dock${expanded ? " expanded" : ""}`}>
      {expanded ? (
        <>
          <button
            type="button"
            className="sched-dock-scrim"
            aria-label="Close scheduled messages"
            onClick={() => setExpanded(false)}
          />
          <div
            className="sched-dock-panel"
            role="dialog"
            aria-label="Scheduled messages"
          >
            <div className="sched-dock-panel-head">
              <span className="sched-dock-panel-title">Scheduled messages</span>
              <span className="sched-dock-count">{messages.length}</span>
              <button
                type="button"
                className="sched-dock-collapse"
                aria-label="Collapse scheduled messages"
                onClick={() => setExpanded(false)}
              >
                <ChevronIcon />
              </button>
            </div>
            <div className="sched-dock-list">
              {messages.map((m) => (
                <div key={m.id} className="sched-dock-item">
                  <span className="sched-dock-item-when">
                    {relativeFromNow(m)}
                    {isRecurring(m) ? (
                      <span
                        className="sched-dock-recur"
                        title={cronToLabel(m.cron, m.timezone)}
                      >
                        ↻
                      </span>
                    ) : null}
                  </span>
                  <div className="sched-dock-item-body">
                    <ExpandableText
                      className="sched-dock-item-text"
                      text={m.text || "(no text)"}
                      collapsedMaxHeight="3em"
                    />
                    {isRecurring(m) ? (
                      <span className="sched-dock-item-cadence">
                        {cronToLabel(m.cron, m.timezone)}
                      </span>
                    ) : null}
                  </div>
                  <button
                    type="button"
                    className="link-button danger-link sched-dock-cancel"
                    onClick={() => void onCancel(m.id)}
                  >
                    Cancel
                  </button>
                </div>
              ))}
            </div>
          </div>
        </>
      ) : null}
      <div className="sched-dock-strip">
        <div className="sched-dock-row">
          <button
            type="button"
            className="sched-dock-toggle"
            aria-expanded={expanded}
            onClick={() => setExpanded((value) => !value)}
          >
            <span className="sched-dock-glyph" aria-hidden>
              ◷
            </span>
            {isRecurring(next) ? (
              <span className="sched-dock-recur" aria-hidden>
                ↻
              </span>
            ) : null}
            <span className="sched-dock-label">{label}</span>
            <span className="sched-dock-when">{relativeFromNow(next)}</span>
            <span className="sched-dock-count">{messages.length}</span>
            <span className="sched-dock-chevron" aria-hidden>
              <ChevronIcon />
            </span>
          </button>
        </div>
      </div>
      <span className="sr-only" aria-live="polite">
        {messages.length} scheduled message{messages.length === 1 ? "" : "s"},
        next {relativeFromNow(next)}
      </span>
    </div>
  );
}

function relativeFromNow(schedule: MessageSchedule): string {
  if (!schedule.scheduled_at) {
    return "";
  }
  const diff = new Date(schedule.scheduled_at).getTime() - Date.now();
  if (diff <= 0) {
    return "any moment";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) {
    return "in <1m";
  }
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
