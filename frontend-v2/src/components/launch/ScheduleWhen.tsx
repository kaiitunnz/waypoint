"use client";

import { useCallback, useId } from "react";

import { defaultRecurrenceState, type RecurrenceState, type TimingMode } from "@waypoint/web-core/recurrence";
import {
  DELAY_PRESETS,
  defaultScheduledAt,
  schedulePreview,
  type OneTimeTiming,
} from "@waypoint/web-core/session/scheduleMessage";

import { RecurrenceFields } from "@/components/schedule/RecurrenceFields";
import { Chip } from "@/components/ui/Chip";
import { Field, TextArea, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";

import styles from "./ScheduleWhen.module.css";

export interface ScheduleState {
  timingMode: TimingMode;
  recurrence: RecurrenceState;
  recurrenceValid: boolean;
  timing: OneTimeTiming;
  delay: string;
  at: string;
  prompt: string;
}

export function initialScheduleState(): ScheduleState {
  return {
    timingMode: "once",
    recurrence: defaultRecurrenceState(),
    recurrenceValid: false,
    timing: "delay",
    delay: "15",
    at: defaultScheduledAt(),
    prompt: "",
  };
}

interface ScheduleWhenProps {
  host: string;
  token: string;
  value: ScheduleState;
  // Must be stable: the recurrence preview reports validity from an effect.
  onChange: (patch: Partial<ScheduleState>) => void;
}

export function ScheduleWhen({ host, token, value, onChange }: ScheduleWhenProps) {
  const id = useId();
  const preview = schedulePreview(value.timing, value.delay, value.at);
  const onValidChange = useCallback((recurrenceValid: boolean) => onChange({ recurrenceValid }), [onChange]);
  return (
    <>
      <section className={styles.stack}>
        <h3 className={styles.heading}>When</h3>
        <RecurrenceFields
          host={host}
          token={token}
          timing={value.timingMode}
          onTimingChange={(timingMode) => onChange({ timingMode })}
          state={value.recurrence}
          onStateChange={(recurrence) => onChange({ recurrence })}
          onValidChange={onValidChange}
        >
          <SegmentedControl
            label="Launch"
            value={value.timing}
            onChange={(timing) => onChange({ timing })}
            segments={[
              { value: "delay", label: "After delay" },
              { value: "datetime", label: "At a time" },
            ]}
          />
          {value.timing === "delay" ? (
            <>
              <div className={styles.chips} role="group" aria-label="Delay presets">
                {DELAY_PRESETS.map((preset) => (
                  <Chip key={preset.minutes} selected={value.delay === String(preset.minutes)} onClick={() => onChange({ delay: String(preset.minutes) })}>
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
                  value={value.delay}
                  onChange={(event) => onChange({ delay: event.target.value })}
                />
              </Field>
            </>
          ) : (
            <Field label="Local time" htmlFor={`${id}-at`}>
              <TextInput id={`${id}-at`} type="datetime-local" value={value.at} onChange={(event) => onChange({ at: event.target.value })} />
            </Field>
          )}
          {preview ? (
            <p className={styles.preview}>
              Launches <strong>{preview.absolute}</strong> {preview.relative}
            </p>
          ) : null}
        </RecurrenceFields>
      </section>
      <Field label="First message" htmlFor={`${id}-prompt`}>
        <TextArea id={`${id}-prompt`} rows={3} value={value.prompt} onChange={(event) => onChange({ prompt: event.target.value })} />
      </Field>
    </>
  );
}
