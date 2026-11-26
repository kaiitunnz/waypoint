"use client";

import { ReactNode, useId, useState } from "react";

import { useRecurrencePreview } from "@waypoint/web-core/hooks/useRecurrencePreview";
import {
  CADENCE_LABELS,
  CADENCES,
  formatInZone,
  RecurrenceState,
  supportedTimezones,
  timezoneAbbrev,
  TimingMode,
  WEEKDAY_LABELS,
} from "@waypoint/web-core/recurrence";

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
  const [zones] = useState<string[]>(() => supportedTimezones());
  const custom = state.cadence === "custom";
  const {
    occurrences,
    error: previewError,
    previewing,
  } = useRecurrencePreview(host, token, timing, state, onValidChange);

  function update(patch: Partial<RecurrenceState>) {
    onStateChange({ ...state, ...patch });
  }

  return (
    <div className="recurrence">
      <div
        className="segmented recurrence__mode"
        role="tablist"
        aria-label="Timing"
      >
        <button
          type="button"
          role="tab"
          aria-selected={timing === "once"}
          className={`segmented-item${timing === "once" ? " active" : ""}`}
          onClick={() => onTimingChange("once")}
        >
          One time
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={timing === "repeat"}
          className={`segmented-item${timing === "repeat" ? " active" : ""}`}
          onClick={() => onTimingChange("repeat")}
        >
          Repeat
        </button>
      </div>

      <div className="recurrence__section">
        {timing === "once" ? (
          children
        ) : (
          <>
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

            {custom ? (
              <label className="field">
                <span>Cron expression</span>
                <input
                  type="text"
                  value={state.customCron}
                  spellCheck={false}
                  autoCapitalize="none"
                  autoCorrect="off"
                  placeholder="0 9 * * 1-5"
                  onChange={(event) =>
                    update({ customCron: event.target.value })
                  }
                />
                <span className="recurrence__hint">
                  Five fields: minute hour day-of-month month day-of-week — e.g.{" "}
                  <code>0 9 * * 1-5</code> is 09:00 on weekdays.
                </span>
              </label>
            ) : null}

            {state.cadence === "weekly" ? (
              <label className="field">
                <span>Day of week</span>
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

            <label className="field">
              <span>Starts</span>
              <input
                type="datetime-local"
                value={state.startAt}
                onChange={(event) => update({ startAt: event.target.value })}
              />
              <span className="recurrence__hint">
                {custom
                  ? "The recurrence begins at or after this; the cron sets the run times."
                  : "Runs at this time of day, repeating from this date."}
              </span>
            </label>

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
          </>
        )}
      </div>
    </div>
  );
}
