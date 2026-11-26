/**
 * Client-side recurrence helpers shared by the schedule-session and
 * schedule-message creation surfaces and the readout rows.
 *
 * Pure string/label logic: maps cadence presets to five-field cron expressions
 * and formats cron + timezone for display. Cron evaluation and occurrence
 * computation live in the backend `/api/schedules/preview` endpoint.
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

export const CADENCE_LABELS: Record<Cadence, string> = {
  daily: "Daily",
  weekdays: "Weekdays",
  weekly: "Weekly",
  monthly: "Monthly",
  custom: "Custom",
};

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
  // "YYYY-MM-DDTHH:MM" wall-clock in `timezone`: the recurrence start, and for
  // the four presets also the time of day.
  startAt: string;
  weekday: number; // 0=Sun … 6=Sat, for the weekly cadence
  dayOfMonth: number; // 1–31, for the monthly cadence
  customCron: string; // raw five-field expression, for the custom cadence
  timezone: string; // IANA zone
}

function defaultStartAt(): string {
  const now = new Date();
  const pad = (n: number) => String(n).padStart(2, "0");
  return (
    `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}` +
    `T09:00`
  );
}

export function defaultRecurrenceState(): RecurrenceState {
  return {
    cadence: "daily",
    startAt: defaultStartAt(),
    weekday: 1,
    dayOfMonth: 1,
    customCron: "0 9 * * 1-5",
    timezone: browserTimezone(),
  };
}

function timeFields(startAt: string): { minute: number; hour: number } | null {
  const match = /T(\d{1,2}):(\d{2})/.exec(startAt);
  if (!match) return null;
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  if (hour < 0 || hour > 23 || minute < 0 || minute > 59) return null;
  return { minute, hour };
}

/**
 * Build a five-field cron expression from the current recurrence state, or
 * `null` when the state is not yet valid enough to submit. The custom
 * expression is passed through verbatim; the backend validates it.
 */
export function cronFromState(state: RecurrenceState): string | null {
  if (state.cadence === "custom") {
    const trimmed = state.customCron.trim();
    return trimmed.length > 0 ? trimmed : null;
  }
  const parsed = timeFields(state.startAt);
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

type CronCadence =
  | { kind: "daily" | "weekdays"; clock: string }
  | { kind: "weekly"; clock: string; weekday: number }
  | { kind: "monthly"; clock: string; day: number };

/** The preset cadence a cron expresses, or null for any other shape. */
function cronCadence(cron: string): CronCadence | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [minute, hour, dom, month, dow] = fields;
  if (!HHMM.exec(`${minute} ${hour}`) || month !== "*") return null;
  const clock = `${hour.padStart(2, "0")}:${minute.padStart(2, "0")}`;
  if (dom === "*" && dow === "*") return { kind: "daily", clock };
  if (dom === "*" && dow === "1-5") return { kind: "weekdays", clock };
  if (dom === "*" && /^[0-6]$/.test(dow)) return { kind: "weekly", clock, weekday: Number(dow) };
  if (/^\d{1,2}$/.test(dom) && dow === "*") return { kind: "monthly", clock, day: Number(dom) };
  return null;
}

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
  if (cron.trim().split(/\s+/).length !== 5) return `CRON ${cron}${zone}`;
  const cadence = cronCadence(cron);
  switch (cadence?.kind) {
    case "daily":
      return `DAILY · ${cadence.clock}${zone}`;
    case "weekdays":
      return `WEEKDAYS · ${cadence.clock}${zone}`;
    case "weekly":
      return `WEEKLY ${WEEKDAY_LABELS[cadence.weekday].toUpperCase()} · ${cadence.clock}${zone}`;
    case "monthly":
      return `MONTHLY ${cadence.day} · ${cadence.clock}${zone}`;
    default:
      return `CUSTOM ${cron}${zone}`;
  }
}

const WEEKDAY_NAMES = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];

/**
 * A cadence in words ("Daily at 02:00", "Mondays at 09:00"), else the cron
 * expression. The zone is named only when it differs from `localZone`.
 */
export function recurrenceSentence(cron: string, timezone: string | null | undefined, localZone: string): string {
  const zone = timezone && timezone !== localZone ? ` (${timezone})` : "";
  const cadence = cronCadence(cron);
  switch (cadence?.kind) {
    case "daily":
      return `Daily at ${cadence.clock}${zone}`;
    case "weekdays":
      return `Weekdays at ${cadence.clock}${zone}`;
    case "weekly":
      return `${WEEKDAY_NAMES[cadence.weekday]} at ${cadence.clock}${zone}`;
    case "monthly":
      return `Monthly on day ${cadence.day} at ${cadence.clock}${zone}`;
    default:
      return `Cron ${cron}${zone}`;
  }
}

export function isRecurring(
  schedule: { cron?: string | null } | null | undefined,
): boolean {
  return Boolean(schedule?.cron);
}

export function isIdle(
  schedule: { trigger?: "time" | "idle" } | null | undefined,
): boolean {
  return schedule?.trigger === "idle";
}
