"use client";

import { useId } from "react";

import type { RecurrenceState, TimingMode } from "@waypoint/web-core/recurrence";
import { DELAY_PRESETS, schedulePreview, type OneTimeTiming } from "@waypoint/web-core/session/scheduleMessage";

import { RecurrenceFields } from "@/components/schedule/RecurrenceFields";
import { Chip } from "@/components/ui/Chip";
import { Field, TextArea, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";

import styles from "./LaunchDialog.module.css";

export interface ScheduleState {
  timingMode: TimingMode;
  recurrence: RecurrenceState;
  recurrenceValid: boolean;
  timing: OneTimeTiming;
  delay: string;
  at: string;
  prompt: string;
}

interface ScheduleWhenProps {
  host: string;
  token: string;
  value: ScheduleState;
  onChange: (patch: Partial<ScheduleState>) => void;
}

// When the session launches, and the first message it gets.
export function ScheduleWhen({ host, token, value, onChange }: ScheduleWhenProps) {
  const id = useId();
  const preview = schedulePreview(value.timing, value.delay, value.at);
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
          onValidChange={(recurrenceValid) => onChange({ recurrenceValid })}
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
              Launches <strong>{preview.absolute}</strong> <span>{preview.relative}</span>
            </p>
          ) : null}
        </RecurrenceFields>
      </section>
      <section className={styles.stack}>
        <Field label="First message" htmlFor={`${id}-prompt`}>
          <TextArea id={`${id}-prompt`} rows={3} value={value.prompt} onChange={(event) => onChange({ prompt: event.target.value })} />
        </Field>
      </section>
    </>
  );
}
