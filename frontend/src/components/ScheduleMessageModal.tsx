"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { RecurrenceControl } from "@/components/RecurrenceControl";
import { createMessageSchedule } from "@/lib/api";
import { trapTabFocus } from "@/lib/keyboard";
import {
  cronFromState,
  defaultRecurrenceState,
  RecurrenceState,
  TimingMode,
} from "@/lib/recurrence";

interface ScheduleMessageModalProps {
  host: string;
  token: string;
  sessionId: string;
  initialDraft?: string;
  onClose: () => void;
  onScheduled?: () => void;
  onError: (message: string) => void;
}

type Timing = "delay" | "datetime";

const DELAY_PRESETS: { label: string; minutes: number }[] = [
  { label: "5m", minutes: 5 },
  { label: "15m", minutes: 15 },
  { label: "30m", minutes: 30 },
  { label: "1h", minutes: 60 },
  { label: "3h", minutes: 180 },
];

// Small portaled form modal for queuing a message to the current session.
// Follows the SshConnectModal conventions: rendered to document.body (so it
// escapes the composer's backdrop-filtered ancestor), Escape to close, a focus
// trap, and focus restored to the trigger on unmount.
export function ScheduleMessageModal({
  host,
  token,
  sessionId,
  initialDraft = "",
  onClose,
  onScheduled,
  onError,
}: ScheduleMessageModalProps) {
  const [draft, setDraft] = useState(initialDraft);
  const [timing, setTiming] = useState<Timing>("delay");
  const [delay, setDelay] = useState("15");
  const [at, setAt] = useState(defaultScheduledAt);
  const [submit, setSubmit] = useState(true);
  const [error, setError] = useState("");
  const [sending, setSending] = useState(false);
  const [timingMode, setTimingMode] = useState<TimingMode>("once");
  const [recurrence, setRecurrence] = useState<RecurrenceState>(
    defaultRecurrenceState,
  );
  const [recurrenceValid, setRecurrenceValid] = useState(false);
  const modalRef = useRef<HTMLDivElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    const previouslyFocused = document.activeElement as HTMLElement | null;
    textareaRef.current?.focus();
    return () => previouslyFocused?.focus();
  }, []);

  useEffect(() => {
    const originalOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = originalOverflow;
    };
  }, []);

  useEffect(() => {
    function handleKeyDown(event: globalThis.KeyboardEvent) {
      if (event.key === "Escape") {
        event.preventDefault();
        onClose();
        return;
      }
      trapTabFocus(event, modalRef.current);
    }
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  async function handleSchedule() {
    const text = draft.trim();
    if (!text || sending) {
      return;
    }
    setError("");
    const options: {
      submit: boolean;
      delaySeconds?: number;
      scheduledAt?: string;
      cron?: string;
      timezone?: string;
      startAt?: string;
    } = { submit };
    if (timingMode === "repeat") {
      const cron = cronFromState(recurrence);
      if (!cron) {
        setError("Enter a valid recurrence.");
        return;
      }
      options.cron = cron;
      options.timezone = recurrence.timezone;
      if (recurrence.startAt) options.startAt = recurrence.startAt;
    } else if (timing === "delay") {
      const minutes = Number.parseFloat(delay);
      if (!Number.isFinite(minutes) || minutes < 0) {
        setError("Enter a non-negative delay in minutes.");
        return;
      }
      options.delaySeconds = Math.round(minutes * 60);
    } else {
      const local = new Date(at);
      if (Number.isNaN(local.getTime())) {
        setError("Enter a valid scheduled time.");
        return;
      }
      options.scheduledAt = local.toISOString();
    }
    setSending(true);
    try {
      await createMessageSchedule(host, token, sessionId, text, options);
      onScheduled?.();
      onClose();
    } catch (err) {
      onError(err instanceof Error ? err.message : "failed to schedule message");
    } finally {
      setSending(false);
    }
  }

  const preview = computeSchedulePreview(timing, delay, at);

  return createPortal(
    <div
      className="schedule-msg-dialog-overlay"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) {
          onClose();
        }
      }}
      role="presentation"
    >
      <div
        ref={modalRef}
        className="schedule-msg-dialog"
        role="dialog"
        aria-modal="true"
        aria-label="Schedule message"
      >
        <div className="schedule-msg-dialog-header">
          <span className="schedule-msg-dialog-title">
            <span className="schedule-msg-dialog-glyph" aria-hidden="true">
              ◷
            </span>
            Schedule message
          </span>
          <button
            type="button"
            className="schedule-msg-dialog-close"
            onClick={onClose}
            aria-label="Close"
          >
            ×
          </button>
        </div>
        <textarea
          ref={textareaRef}
          className="schedule-msg-dialog-input"
          rows={3}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder="Message text…"
          aria-label="Message text"
        />
        <RecurrenceControl
          host={host}
          token={token}
          timing={timingMode}
          onTimingChange={setTimingMode}
          state={recurrence}
          onStateChange={setRecurrence}
          onValidChange={setRecurrenceValid}
        >
          <div className="segmented schedule-msg-modes" role="tablist">
            <button
              type="button"
              role="tab"
              aria-selected={timing === "delay"}
              className={`segmented-item${timing === "delay" ? " active" : ""}`}
              onClick={() => setTiming("delay")}
            >
              After delay
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={timing === "datetime"}
              className={`segmented-item${
                timing === "datetime" ? " active" : ""
              }`}
              onClick={() => setTiming("datetime")}
            >
              At a time
            </button>
          </div>
          {timing === "delay" ? (
            <div className="schedule-msg-delay">
              <div className="schedule-msg-presets">
                {DELAY_PRESETS.map((preset) => (
                  <button
                    key={preset.minutes}
                    type="button"
                    className={`schedule-msg-preset${
                      delay === String(preset.minutes) ? " active" : ""
                    }`}
                    onClick={() => setDelay(String(preset.minutes))}
                  >
                    {preset.label}
                  </button>
                ))}
              </div>
              <label className="schedule-msg-field-inline">
                <input
                  type="number"
                  className="schedule-msg-dialog-number"
                  min={0}
                  step={1}
                  value={delay}
                  onChange={(e) => setDelay(e.target.value)}
                  placeholder="15"
                  aria-label="Delay in minutes"
                />
                <span className="schedule-msg-field-suffix">minutes</span>
              </label>
            </div>
          ) : (
            <label className="schedule-msg-field">
              <span className="schedule-msg-field-label">Local time</span>
              <input
                type="datetime-local"
                className="schedule-msg-dialog-number"
                value={at}
                onChange={(e) => setAt(e.target.value)}
                aria-label="Scheduled local time"
              />
            </label>
          )}
          {preview ? (
            <p className="schedule-msg-preview">
              <span className="schedule-msg-preview-glyph" aria-hidden="true">
                →
              </span>
              Sends&nbsp;<strong>{preview.absolute}</strong>
              <span className="schedule-msg-preview-rel">
                {preview.relative}
              </span>
            </p>
          ) : null}
        </RecurrenceControl>
        <div className="schedule-msg-submit-row">
          <span className="schedule-msg-submit-label">On delivery</span>
          <div className="segmented segmented-quiet schedule-msg-submit">
            <button
              type="button"
              className={`segmented-item${submit ? " active" : ""}`}
              onClick={() => setSubmit(true)}
            >
              Submit
            </button>
            <button
              type="button"
              className={`segmented-item${!submit ? " active" : ""}`}
              onClick={() => setSubmit(false)}
            >
              Hold as draft
            </button>
          </div>
        </div>
        {error ? (
          <p className="error schedule-msg-dialog-error" role="alert">
            {error}
          </p>
        ) : null}
        <div className="schedule-msg-dialog-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="primary"
            onClick={() => void handleSchedule()}
            disabled={
              !draft.trim() ||
              sending ||
              (timingMode === "repeat" && !recurrenceValid)
            }
          >
            {sending
              ? "Scheduling…"
              : timingMode === "repeat"
                ? "Create recurrence"
                : "Schedule once"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}

function defaultScheduledAt(): string {
  const date = new Date();
  date.setMinutes(date.getMinutes() + 15);
  const pad = (value: number) => value.toString().padStart(2, "0");
  return (
    `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}` +
    `T${pad(date.getHours())}:${pad(date.getMinutes())}`
  );
}

function computeSchedulePreview(
  timing: Timing,
  delay: string,
  at: string,
): { absolute: string; relative: string } | null {
  let target: Date;
  if (timing === "delay") {
    const minutes = Number.parseFloat(delay);
    if (!Number.isFinite(minutes) || minutes < 0) {
      return null;
    }
    target = new Date(Date.now() + minutes * 60_000);
  } else {
    target = new Date(at);
    if (Number.isNaN(target.getTime())) {
      return null;
    }
  }
  const sameDay = new Date().toDateString() === target.toDateString();
  const absolute = target.toLocaleString(undefined, {
    weekday: sameDay ? undefined : "short",
    month: sameDay ? undefined : "short",
    day: sameDay ? undefined : "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
  return { absolute, relative: relativeFromNow(target) };
}

function relativeFromNow(target: Date): string {
  const diff = target.getTime() - Date.now();
  if (diff <= 0) {
    return "now";
  }
  const minutes = Math.round(diff / 60_000);
  if (minutes < 1) {
    return "in under a minute";
  }
  if (minutes < 60) {
    return `in ${minutes} min`;
  }
  const hours = Math.floor(minutes / 60);
  const rem = minutes % 60;
  if (hours < 48) {
    return rem ? `in ${hours}h ${rem}m` : `in ${hours}h`;
  }
  const days = Math.round(hours / 24);
  return `in ${days} days`;
}
