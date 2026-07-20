/**
 * Client-side recurrence helpers shared by the schedule-session and
 * schedule-message creation surfaces and the readout rows.
 *
 * This module is pure string/label logic: it maps friendly cadence presets to
 * canonical five-field cron expressions and formats cron + timezone for
 * display. It deliberately does NOT evaluate cron or compute occurrences — the
 * backend `/api/schedules/preview` endpoint is the single authority for that,
 * so the preview always matches what the scheduler actually fires.
 */

export type TimingMode = "once" | "repeat";
export type Cadence = "daily" | "weekdays" | "weekly" | "monthly" | "custom";

export const CADENCES: Cadence[] = [
  "daily",
  "weekdays",
  "weekly",
  "monthly",
  "custom",
];

export const WEEKDAY_LABELS = [
  "Sun",
  "Mon",
  "Tue",
  "Wed",
  "Thu",
  "Fri",
  "Sat",
];

export interface RecurrenceState {
  cadence: Cadence;
  time: string; // "HH:mm" local wall-clock, for the four presets
  weekday: number; // 0=Sun … 6=Sat, for the weekly cadence
  dayOfMonth: number; // 1–31, for the monthly cadence
  customCron: string; // raw five-field expression, for the custom cadence
  timezone: string; // IANA zone
}

export function defaultRecurrenceState(): RecurrenceState {
  return {
    cadence: "daily",
    time: "09:00",
    weekday: 1,
    dayOfMonth: 1,
    customCron: "0 9 * * 1-5",
    timezone: browserTimezone(),
  };
}

function parseTime(time: string): { minute: number; hour: number } | null {
  const match = /^(\d{1,2}):(\d{2})$/.exec(time.trim());
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { minute, hour };
}

/**
 * Build a five-field cron expression from the current recurrence state, or
 * `null` when the state is not yet valid enough to submit (bad time, empty
 * custom expression). The custom expression is passed through verbatim; the
 * backend remains authoritative for validating it.
 */
export function cronFromState(state: RecurrenceState): string | null {
  if (state.cadence === "custom") {
    const trimmed = state.customCron.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  const parsed = parseTime(state.time);
  if (!parsed) return null;
  const { minute, hour } = parsed;
  switch (state.cadence) {
    case "daily":
      return `${minute} ${hour} * * *`;
    case "weekdays":
      return `${minute} ${hour} * * 1-5`;
    case "weekly":
      return `${minute} ${hour} * * ${state.weekday}`;
    case "monthly":
      return `${minute} ${hour} ${state.dayOfMonth} * *`;
  }
}

export function browserTimezone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

/**
 * All IANA zones the runtime knows, with the browser zone guaranteed present
 * and first. Falls back to just the browser zone on older engines.
 */
export function supportedTimezones(): string[] {
  const browser = browserTimezone();
  const withValues = Intl as typeof Intl & {
    supportedValuesOf?: (key: string) => string[];
  };
  let zones: string[] = [];
  try {
    zones = withValues.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    zones = [];
  }
  if (zones.length === 0) return [browser];
  return [browser, ...zones.filter((z) => z !== browser)];
}

/** Short timezone abbreviation for a moment in a zone, e.g. "EDT", "GMT+8". */
export function timezoneAbbrev(
  when: Date,
  timezone: string | null | undefined,
): string {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: timezone ?? undefined,
      timeZoneName: "short",
    }).formatToParts(when);
    return parts.find((p) => p.type === "timeZoneName")?.value ?? (timezone ?? "");
  } catch {
    return timezone ?? "";
  }
}

/** Format an instant in a specific IANA zone: "Jul 21, 09:00". */
export function formatInZone(
  when: Date,
  timezone: string | null | undefined,
): string {
  try {
    return when.toLocaleString(undefined, {
      timeZone: timezone ?? undefined,
      month: "short",
      day: "numeric",
      hour: "2-digit",
      minute: "2-digit",
    });
  } catch {
    return when.toLocaleString();
  }
}

const HHMM = /^(\d{1,2}) (\d{1,2})$/;

/**
 * A compact, uppercase cadence label for a stored cron + timezone, recognizing
 * the canonical preset shapes and otherwise showing the raw expression. Shape:
 * `WEEKDAYS · 09:00 · America/New_York`.
 */
export function cronToLabel(
  cron: string | null | undefined,
  timezone?: string | null,
): string {
  if (!cron) return "";
  const zone = timezone ? ` · ${timezone}` : "";
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return `CRON ${cron}${zone}`;
  const [minute, hour, dom, month, dow] = fields;
  const clock = HHMM.exec(`${minute} ${hour}`)
    ? `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`
    : null;
  if (clock && month === "*") {
    if (dom === "*" && dow === "*") return `DAILY · ${clock}${zone}`;
    if (dom === "*" && dow === "1-5") return `WEEKDAYS · ${clock}${zone}`;
    if (dom === "*" && /^[0-6]$/.test(dow)) {
      return `WEEKLY ${WEEKDAY_LABELS[Number(dow)].toUpperCase()} · ${clock}${zone}`;
    }
    if (/^\d{1,2}$/.test(dom) && dow === "*") {
      return `MONTHLY ${dom} · ${clock}${zone}`;
    }
  }
  return `CUSTOM ${cron}${zone}`;
}

export function isRecurring(
  schedule: { cron?: string | null } | null | undefined,
): boolean {
  return Boolean(schedule?.cron);
}
