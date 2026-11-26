"use client";

import { useId, useState, type ReactNode } from "react";

import { useRecurrencePreview } from "@waypoint/web-core/hooks/useRecurrencePreview";
import {
  CADENCE_LABELS,
  CADENCES,
  formatInZone,
  supportedTimezones,
  timezoneAbbrev,
  WEEKDAY_LABELS,
  type RecurrenceState,
  type TimingMode,
} from "@waypoint/web-core/recurrence";

import { Chip } from "@/components/ui/Chip";
import { Field, TextInput } from "@/components/ui/Field";
import { SegmentedControl } from "@/components/ui/SegmentedControl";
import { Select } from "@/components/ui/Select";

import styles from "./RecurrenceFields.module.css";

interface RecurrenceFieldsProps {
  host: string;
  token: string;
  timing: TimingMode;
  onTimingChange: (mode: TimingMode) => void;
  state: RecurrenceState;
  onStateChange: (state: RecurrenceState) => void;
  // Whether the recurrence previews cleanly; always true for a one-time send.
  onValidChange: (valid: boolean) => void;
  // The one-time inputs, which differ by what is scheduled.
  children: ReactNode;
}

export function RecurrenceFields({
  host,
  token,
  timing,
  onTimingChange,
  state,
  onStateChange,
  onValidChange,
  children,
}: RecurrenceFieldsProps) {
  const id = useId();
  const [zones] = useState(() => supportedTimezones());
  const { occurrences, error, previewing } = useRecurrencePreview(host, token, timing, state, onValidChange);
  const update = (patch: Partial<RecurrenceState>) => onStateChange({ ...state, ...patch });

  return (
    <div className={styles.fields}>
      <SegmentedControl
        label="Timing"
        value={timing}
        onChange={onTimingChange}
        segments={[
          { value: "once", label: "One time" },
          { value: "repeat", label: "Repeat" },
        ]}
      />
      {timing === "once" ? (
        children
      ) : (
        <>
          <div className={styles.chips} role="group" aria-label="Cadence">
            {CADENCES.map((cadence) => (
              <Chip key={cadence} selected={state.cadence === cadence} onClick={() => update({ cadence })}>
                {cadence === "custom" ? "Cron" : CADENCE_LABELS[cadence]}
              </Chip>
            ))}
          </div>
          {state.cadence === "custom" ? (
            <Field label="Cron expression" htmlFor={`${id}-cron`}>
              <TextInput
                id={`${id}-cron`}
                mono
                value={state.customCron}
                spellCheck={false}
                autoCapitalize="none"
                autoCorrect="off"
                onChange={(event) => update({ customCron: event.target.value })}
              />
            </Field>
          ) : null}
          {state.cadence === "weekly" ? (
            <Field label="Day of week" htmlFor={`${id}-weekday`}>
              <Select
                id={`${id}-weekday`}
                value={state.weekday}
                onChange={(event) => update({ weekday: Number(event.target.value) })}
              >
                {WEEKDAY_LABELS.map((label, index) => (
                  <option key={label} value={index}>
                    {label}
                  </option>
                ))}
              </Select>
            </Field>
          ) : null}
          {state.cadence === "monthly" ? (
            <Field label="Day of month" htmlFor={`${id}-day`}>
              <TextInput
                id={`${id}-day`}
                type="number"
                inputMode="numeric"
                min={1}
                max={31}
                value={state.dayOfMonth}
                onChange={(event) => update({ dayOfMonth: Math.min(31, Math.max(1, Number(event.target.value) || 1)) })}
              />
            </Field>
          ) : null}
          <Field label="Starts" htmlFor={`${id}-start`}>
            <TextInput
              id={`${id}-start`}
              type="datetime-local"
              value={state.startAt}
              onChange={(event) => update({ startAt: event.target.value })}
            />
          </Field>
          <Field label="Time zone" htmlFor={`${id}-zone`}>
            <TextInput
              id={`${id}-zone`}
              list={`${id}-zones`}
              value={state.timezone}
              spellCheck={false}
              autoCapitalize="none"
              autoCorrect="off"
              onChange={(event) => update({ timezone: event.target.value })}
            />
            <datalist id={`${id}-zones`}>
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </Field>
          <div className={styles.preview} aria-live="polite">
            <span className={styles.previewLabel}>Next runs</span>
            {error ? (
              <p className={styles.error}>{error}</p>
            ) : occurrences.length > 0 ? (
              <ul className={styles.runs}>
                {occurrences.map((iso) => {
                  const when = new Date(iso);
                  return (
                    <li key={iso}>
                      {formatInZone(when, state.timezone)} <span>{timezoneAbbrev(when, state.timezone)}</span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className={styles.previewLabel}>{previewing ? "Calculating…" : "Enter a valid recurrence."}</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
