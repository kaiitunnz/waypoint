"use client";

import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";

import { RecurrenceControl } from "@/components/RecurrenceControl";
import { createMessageSchedule } from "@waypoint/web-core/api";
import { trapTabFocus } from "@waypoint/web-core/keyboard";
import { defaultRecurrenceState, RecurrenceState, TimingMode } from "@waypoint/web-core/recurrence";
import {
  DELAY_PRESETS,
  defaultScheduledAt,
  scheduleMessageOptions,
  schedulePreview,
  type OneTimeTiming,
  type ScheduleTrigger,
} from "@waypoint/web-core/session/scheduleMessage";
import { IDLE_SEND_MODES, type IdleBatchMode } from "@waypoint/web-core/session/sendModes";

interface ScheduleMessageModalProps {
  host: string;
  token: string;
  sessionId: string;
  initialDraft?: string;
  onClose: () => void;
  onScheduled?: () => void;
  onError: (message: string) => void;
}

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
  const [trigger, setTrigger] = useState<ScheduleTrigger>("time");
  const [idleBatch, setIdleBatch] = useState<IdleBatchMode>("next_cycle");
  const [timing, setTiming] = useState<OneTimeTiming>("delay");
  const [delay, setDelay] = useState("15");
  const [at, setAt] = useState(() => defaultScheduledAt());
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
    const built = scheduleMessageOptions({
      trigger,
      idleBatch,
      timingMode,
      recurrence,
      timing,
      delay,
      at,
      submit,
    });
    if ("error" in built) {
      setError(built.error);
      return;
    }
    setSending(true);
    try {
      await createMessageSchedule(host, token, sessionId, text, built.options);
      onScheduled?.();
      onClose();
    } catch (err) {
      onError(err instanceof Error ? err.message : "failed to schedule message");
    } finally {
      setSending(false);
    }
  }

  const preview = schedulePreview(timing, delay, at);

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
        <div className="schedule-msg-trigger-row">
          <span className="schedule-msg-submit-label">Trigger</span>
          <div className="segmented schedule-msg-trigger" role="tablist" aria-label="Trigger">
            <button
              type="button"
              role="tab"
              aria-selected={trigger === "time"}
              className={`segmented-item${trigger === "time" ? " active" : ""}`}
              onClick={() => setTrigger("time")}
            >
              Timed
            </button>
            <button
              type="button"
              role="tab"
              aria-selected={trigger === "idle"}
              className={`segmented-item${trigger === "idle" ? " active" : ""}`}
              onClick={() => setTrigger("idle")}
            >
              When idle
            </button>
          </div>
        </div>
        {trigger === "idle" ? (
          <div className="schedule-msg-idle">
            <div
              className="segmented schedule-msg-idle-batch"
              role="tablist"
              aria-label="Idle batch"
            >
              {IDLE_SEND_MODES.map((batch) => (
                <button
                  key={batch.value}
                  type="button"
                  role="tab"
                  aria-selected={idleBatch === batch.value}
                  className={`segmented-item${idleBatch === batch.value ? " active" : ""}`}
                  onClick={() => setIdleBatch(batch.value)}
                >
                  {batch.label}
                </button>
              ))}
            </div>
            <p className="schedule-msg-idle-hint">
              {IDLE_SEND_MODES.find((b) => b.value === idleBatch)?.hint}.
            </p>
          </div>
        ) : (
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
        )}
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
              (trigger === "time" && timingMode === "repeat" && !recurrenceValid)
            }
          >
            {sending ? "Scheduling…" : "Schedule"}
          </button>
        </div>
      </div>
    </div>,
    document.body,
  );
}
