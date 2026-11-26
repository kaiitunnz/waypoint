"use client";

import { useId, useState } from "react";

import { createMessageSchedule, isAuthError } from "@waypoint/web-core/api";
import { defaultRecurrenceState, type RecurrenceState, type TimingMode } from "@waypoint/web-core/recurrence";
import {
  DELAY_PRESETS,
  defaultScheduledAt,
  scheduleMessageOptions,
  schedulePreview,
  type OneTimeTiming,
  type ScheduleTrigger,
} from "@waypoint/web-core/session/scheduleMessage";
import { IDLE_SEND_MODES, type IdleBatchMode } from "@waypoint/web-core/session/sendModes";

import { RecurrenceFields } from "@/components/schedule/RecurrenceFields";
import { Button } from "@/components/ui/Button";
import { Chip } from "@/components/ui/Chip";
import { Dialog } from "@/components/ui/Dialog";
import { Field, TextArea, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { useToast } from "@/components/ui/Toast";
import { handleAuthFailure } from "@/lib/auth";
import { usePointerUI } from "@/lib/layout";
import { useMountedRef } from "@/lib/useMountedRef";

import styles from "./ScheduleMessageDialog.module.css";

interface ScheduleMessageDialogProps {
  host: string;
  token: string;
  sessionId: string;
  initialText: string;
  // False on a stopped session, which cannot queue an idle message.
  canQueue: boolean;
  onClose: () => void;
  onScheduled: () => void;
}

export function ScheduleMessageDialog({
  host,
  token,
  sessionId,
  initialText,
  canQueue,
  onClose,
  onScheduled,
}: ScheduleMessageDialogProps) {
  const id = useId();
  const pointer = usePointerUI();
  const { toast } = useToast();
  const [text, setText] = useState(initialText);
  const [chosen, setTrigger] = useState<ScheduleTrigger>("time");
  const trigger = canQueue ? chosen : "time";
  const [idleBatch, setIdleBatch] = useState<IdleBatchMode>("next_cycle");
  const [timing, setTiming] = useState<OneTimeTiming>("delay");
  const [delay, setDelay] = useState("15");
  const [at, setAt] = useState(() => defaultScheduledAt());
  const [submit, setSubmit] = useState(true);
  const [timingMode, setTimingMode] = useState<TimingMode>("once");
  const [recurrence, setRecurrence] = useState<RecurrenceState>(defaultRecurrenceState);
  const [recurrenceValid, setRecurrenceValid] = useState(false);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const shown = useMountedRef();
  const preview = schedulePreview(timing, delay, at);
  const idle = IDLE_SEND_MODES.find((mode) => mode.value === idleBatch);
  const blocked = !text.trim() || (trigger === "time" && timingMode === "repeat" && !recurrenceValid);

  async function schedule() {
    const message = text.trim();
    if (!message || saving) return;
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
    setSaving(true);
    try {
      await createMessageSchedule(host, token, sessionId, message, built.options);
      onScheduled();
      toast({
        message:
          trigger === "idle"
            ? "Message scheduled for when idle"
            : timingMode === "repeat"
              ? "Recurring message scheduled"
              : `Message scheduled for ${preview?.absolute ?? "later"}`,
      });
      if (shown.current) onClose();
    } catch (err) {
      if (isAuthError(err)) {
        handleAuthFailure();
        return;
      }
      const message = err instanceof Error ? err.message : "failed to schedule message";
      if (shown.current) setError(message);
      else toast({ message, tone: "error" });
    } finally {
      setSaving(false);
    }
  }

  return (
    <Dialog
      open
      onClose={onClose}
      title="Schedule message"
      size="lg"
      footer={
        <>
          {pointer ? (
            <Button variant="text" onClick={onClose}>
              Cancel
            </Button>
          ) : null}
          <Button variant="primary" block={!pointer} busy={saving} disabled={blocked} onClick={() => void schedule()}>
            {saving ? "Scheduling…" : "Schedule"}
          </Button>
        </>
      }
    >
      <div className={styles.form}>
        <Field label="Message" htmlFor={`${id}-text`}>
          <TextArea
            id={`${id}-text`}
            rows={3}
            value={text}
            data-autofocus=""
            onChange={(event) => setText(event.target.value)}
          />
        </Field>
        {canQueue ? (
          <div className={styles.row}>
            <span className={styles.label}>Trigger</span>
            <SegmentedControl
              label="Trigger"
              value={trigger}
              onChange={setTrigger}
              segments={[
                { value: "time", label: "Timed" },
                { value: "idle", label: "When idle" },
              ]}
            />
          </div>
        ) : null}
        {trigger === "idle" ? (
          <div className={styles.idle}>
            <SegmentedControl
              label="Idle delivery"
              value={idleBatch}
              onChange={setIdleBatch}
              segments={IDLE_SEND_MODES.map((mode) => ({ value: mode.value, label: mode.label }))}
            />
            <p className={styles.label}>{idle?.hint}.</p>
          </div>
        ) : (
          <RecurrenceFields
            host={host}
            token={token}
            timing={timingMode}
            onTimingChange={setTimingMode}
            state={recurrence}
            onStateChange={setRecurrence}
            onValidChange={setRecurrenceValid}
          >
            <SegmentedControl
              label="Send"
              value={timing}
              onChange={setTiming}
              segments={[
                { value: "delay", label: "After delay" },
                { value: "datetime", label: "At a time" },
              ]}
            />
            {timing === "delay" ? (
              <div className={styles.delay}>
                <div className={styles.presets} role="group" aria-label="Delay presets">
                  {DELAY_PRESETS.map((preset) => (
                    <Chip
                      key={preset.minutes}
                      selected={delay === String(preset.minutes)}
                      onClick={() => setDelay(String(preset.minutes))}
                    >
                      {preset.label}
                    </Chip>
                  ))}
                </div>
                <Field label="Delay in minutes" htmlFor={`${id}-delay`}>
                  <TextInput
                    id={`${id}-delay`}
                    type="number"
                    inputMode="decimal"
                    min={0}
                    step={1}
                    value={delay}
                    onChange={(event) => setDelay(event.target.value)}
                  />
                </Field>
              </div>
            ) : (
              <Field label="Local time" htmlFor={`${id}-at`}>
                <TextInput id={`${id}-at`} type="datetime-local" value={at} onChange={(event) => setAt(event.target.value)} />
              </Field>
            )}
            {preview ? (
              <p className={styles.preview}>
                Sends <strong>{preview.absolute}</strong> <span>{preview.relative}</span>
              </p>
            ) : null}
          </RecurrenceFields>
        )}
        <div className={styles.row}>
          <span className={styles.label}>On delivery</span>
          <SegmentedControl
            label="On delivery"
            value={submit ? "submit" : "draft"}
            onChange={(value) => setSubmit(value === "submit")}
            segments={[
              { value: "submit", label: "Submit" },
              { value: "draft", label: "Hold as draft" },
            ]}
          />
        </div>
        {error ? (
          <p className={styles.error} role="alert">
            {error}
          </p>
        ) : null}
      </div>
    </Dialog>
  );
}
