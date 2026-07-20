"use client";

import { ReactNode, useEffect, useId, useState } from "react";

import { previewSchedule } from "@/lib/api";
import {
  Cadence,
  CADENCES,
  cronFromState,
  formatInZone,
  RecurrenceState,
  supportedTimezones,
  timezoneAbbrev,
  TimingMode,
  WEEKDAY_LABELS,
} from "@/lib/recurrence";

const CADENCE_LABELS: Record<Cadence, string> = {
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
  custom: "Custom",
};

interface RecurrenceControlProps {
  host: string;
  token: string;
  timing: TimingMode;
  onTimingChange: (mode: TimingMode) => void;
  state: RecurrenceState;
  onStateChange: (state: RecurrenceState) => void;
  // Reports whether the current recurrence previews cleanly, so the parent can
  // gate its submit button. Always true while timing is "once".
  onValidChange: (valid: boolean) => void;
  // The one-time timing UI, owned by the parent (delay/datetime differ per
  // surface). Rendered only while timing is "once".
  children: ReactNode;
}

export function RecurrenceControl({
  host,
  token,
  timing,
  onTimingChange,
  state,
  onStateChange,
  onValidChange,
  children,
}: RecurrenceControlProps) {
  const uid = useId();
  const tzListId = `${uid}-tz`;
  const [occurrences, setOccurrences] = useState<string[]>([]);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [previewing, setPreviewing] = useState(false);
  const [zones] = useState<string[]>(() => supportedTimezones());

  const cron = timing === "repeat" ? cronFromState(state) : null;

  // Debounced server preview — the backend is authoritative for cron/DST, so
  // the preview always matches what the scheduler will fire.
  useEffect(() => {
    if (timing !== "repeat") {
      onValidChange(true);
      setPreviewError(null);
      setOccurrences([]);
      return;
    }
    if (!cron) {
      onValidChange(false);
      setPreviewError(null);
      setOccurrences([]);
      return;
    }
    let cancelled = false;
    setPreviewing(true);
    const handle = window.setTimeout(async () => {
      try {
        const next = await previewSchedule(host, token, cron, state.timezone, 3);
        if (cancelled) return;
        setOccurrences(next);
        setPreviewError(null);
        onValidChange(true);
      } catch (error) {
        if (cancelled) return;
        setOccurrences([]);
        setPreviewError(error instanceof Error ? error.message : "invalid recurrence");
        onValidChange(false);
      } finally {
        if (!cancelled) setPreviewing(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [timing, cron, state.timezone, host, token, onValidChange]);

  function update(patch: Partial<RecurrenceState>) {
    onStateChange({ ...state, ...patch });
  }

  return (
    <div className="recurrence">
      <div className="schedule-mode-row" role="tablist" aria-label="Timing">
        <button
          type="button"
          role="tab"
          aria-selected={timing === "once"}
          className={timing === "once" ? "primary" : "secondary"}
          onClick={() => onTimingChange("once")}
        >
          One time
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={timing === "repeat"}
          className={timing === "repeat" ? "primary" : "secondary"}
          onClick={() => onTimingChange("repeat")}
        >
          Repeat
        </button>
      </div>

      {timing === "once" ? (
        children
      ) : (
        <div className="recurrence__body">
          <div
            className="recurrence__cadences"
            role="tablist"
            aria-label="Cadence"
          >
            {CADENCES.map((cadence) => (
              <button
                key={cadence}
                type="button"
                role="tab"
                aria-selected={state.cadence === cadence}
                className={`recurrence__chip${
                  state.cadence === cadence ? " is-active" : ""
                }`}
                onClick={() => update({ cadence })}
              >
                {CADENCE_LABELS[cadence]}
              </button>
            ))}
          </div>

          {state.cadence === "custom" ? (
            <label className="field">
              <span>Cron expression</span>
              <input
                type="text"
                value={state.customCron}
                spellCheck={false}
                autoCapitalize="none"
                autoCorrect="off"
                placeholder="0 9 * * 1-5"
                onChange={(event) => update({ customCron: event.target.value })}
              />
              <span className="recurrence__hint">
                Five fields: minute hour day-of-month month day-of-week — e.g.{" "}
                <code>0 9 * * 1-5</code> is 09:00 on weekdays.
              </span>
            </label>
          ) : (
            <div className="recurrence__preset-fields">
              <label className="field">
                <span>Time</span>
                <input
                  type="time"
                  value={state.time}
                  onChange={(event) => update({ time: event.target.value })}
                />
              </label>
              {state.cadence === "weekly" ? (
                <label className="field">
                  <span>Day</span>
                  <select
                    value={state.weekday}
                    onChange={(event) =>
                      update({ weekday: Number(event.target.value) })
                    }
                  >
                    {WEEKDAY_LABELS.map((label, index) => (
                      <option key={label} value={index}>
                        {label}
                      </option>
                    ))}
                  </select>
                </label>
              ) : null}
              {state.cadence === "monthly" ? (
                <label className="field">
                  <span>Day of month</span>
                  <input
                    type="number"
                    min="1"
                    max="31"
                    step="1"
                    value={state.dayOfMonth}
                    onChange={(event) =>
                      update({
                        dayOfMonth: Math.min(
                          31,
                          Math.max(1, Number(event.target.value) || 1),
                        ),
                      })
                    }
                  />
                </label>
              ) : null}
            </div>
          )}

          <label className="field">
            <span>Timezone</span>
            <input
              type="text"
              list={tzListId}
              value={state.timezone}
              spellCheck={false}
              autoCapitalize="none"
              autoCorrect="off"
              onChange={(event) => update({ timezone: event.target.value })}
            />
            <datalist id={tzListId}>
              {zones.map((zone) => (
                <option key={zone} value={zone} />
              ))}
            </datalist>
          </label>

          <div className="recurrence__preview" aria-live="polite">
            <span className="recurrence__preview-label">Next runs</span>
            {previewError ? (
              <p className="recurrence__error">{previewError}</p>
            ) : occurrences.length > 0 ? (
              <ul className="recurrence__preview-list">
                {occurrences.map((iso) => {
                  const when = new Date(iso);
                  return (
                    <li key={iso}>
                      {formatInZone(when, state.timezone)}{" "}
                      <span className="recurrence__preview-tz">
                        {timezoneAbbrev(when, state.timezone)}
                      </span>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <p className="recurrence__preview-empty">
                {previewing ? "Calculating…" : "Enter a valid recurrence."}
              </p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
