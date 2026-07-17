"use client";

import {
  UNIFIED_TOKEN_CATEGORIES,
  UNIFIED_TOKEN_LABELS,
  UnifiedTokenCategory,
} from "@/lib/tokens";
import {
  InsightSeverity,
  InstanceStorageCategory,
  LifecycleTransition,
  TelemetryFactKind,
  TelemetryParentScope,
  TelemetryRange,
  TokenCoverage,
  TokenGroupBy,
  TurnKind,
} from "@/lib/types";
import { UsageTone } from "@/lib/usage";

// ── Range + filter state ─────────────────────────────────────────────────
//
// Mirrors the shared query params every `/api/telemetry/*` endpoint reads
// (CONTRACT.md §4 / backend/src/waypoint/telemetry/query.py). The frontend
// never resolves the range itself — it sends a preset (or a custom
// start/end) and always renders the `range`/`filters_echo` a response
// echoes back, never its own guess.

export type TelemetryRangePreset = "today" | "7d" | "30d" | "custom";

export interface TelemetryRangeState {
  preset: TelemetryRangePreset;
  // Bare YYYY-MM-DD strings (host-tz calendar days per query.py's
  // `_parse_instant`); only read when preset === "custom".
  start: string;
  end: string;
}

export interface TelemetryFiltersState {
  backends: string[];
  models: string[];
  repos: string[];
  // `key:value` terms (backend/src/waypoint/telemetry/store.py `_parse_tag_term`).
  tags: string[];
  sources: string[];
  transports: string[];
  parentScope: TelemetryParentScope;
  parentSessionId: string;
  includeDescendants: boolean;
}

export const RANGE_PRESET_OPTIONS: { value: TelemetryRangePreset; label: string }[] = [
  { value: "today", label: "Today" },
  { value: "7d", label: "7 days" },
  { value: "30d", label: "30 days" },
  { value: "custom", label: "Custom" },
];

export const DEFAULT_RANGE: TelemetryRangeState = { preset: "7d", start: "", end: "" };

export const DEFAULT_FILTERS: TelemetryFiltersState = {
  backends: [],
  models: [],
  repos: [],
  tags: [],
  sources: [],
  transports: [],
  parentScope: "all",
  parentSessionId: "",
  includeDescendants: true,
};

export function hasActiveFilters(filters: TelemetryFiltersState): boolean {
  return (
    filters.backends.length > 0 ||
    filters.models.length > 0 ||
    filters.repos.length > 0 ||
    filters.tags.length > 0 ||
    filters.sources.length > 0 ||
    filters.transports.length > 0 ||
    filters.parentScope !== "all" ||
    Boolean(filters.parentSessionId.trim())
  );
}

// Builds the shared range+filter query params every endpoint reads. `extra`
// layers endpoint-specific params (group_by, kind, page, ...) on the same base.
export function telemetryParams(
  range: TelemetryRangeState,
  filters: TelemetryFiltersState,
  extra?: Record<string, string | number | boolean | undefined | null>,
): URLSearchParams {
  const params = new URLSearchParams();
  if (range.preset === "custom") {
    params.set("preset", "custom");
    if (range.start) params.set("start", range.start);
    if (range.end) params.set("end", range.end);
  } else {
    params.set("preset", range.preset);
  }
  for (const backend of filters.backends) params.append("backend", backend);
  for (const model of filters.models) params.append("model", model);
  for (const repo of filters.repos) params.append("repo", repo);
  for (const tag of filters.tags) params.append("tag", tag);
  for (const source of filters.sources) params.append("source", source);
  for (const transport of filters.transports) params.append("transport", transport);
  if (filters.parentScope !== "all") params.set("scope", filters.parentScope);
  const parentSessionId = filters.parentSessionId.trim();
  if (parentSessionId) params.set("parent", parentSessionId);
  if (!filters.includeDescendants) params.set("descendants", "false");
  if (extra) {
    for (const [key, value] of Object.entries(extra)) {
      if (value === undefined || value === null) continue;
      params.set(key, String(value));
    }
  }
  return params;
}

// ── Persistence (mirrors lib/store.ts conventions) ───────────────────────

const QUERY_KEY = "waypoint.telemetry-query";

interface StoredTelemetryQuery {
  range: TelemetryRangeState;
  filters: TelemetryFiltersState;
}

export function readTelemetryQuery(): { range: TelemetryRangeState; filters: TelemetryFiltersState } {
  if (typeof window === "undefined") {
    return { range: DEFAULT_RANGE, filters: DEFAULT_FILTERS };
  }
  const raw = window.localStorage.getItem(QUERY_KEY);
  if (!raw) {
    return { range: DEFAULT_RANGE, filters: DEFAULT_FILTERS };
  }
  try {
    const parsed = JSON.parse(raw) as Partial<StoredTelemetryQuery>;
    return {
      range: { ...DEFAULT_RANGE, ...parsed.range },
      filters: { ...DEFAULT_FILTERS, ...parsed.filters },
    };
  } catch {
    return { range: DEFAULT_RANGE, filters: DEFAULT_FILTERS };
  }
}

export function writeTelemetryQuery(
  range: TelemetryRangeState,
  filters: TelemetryFiltersState,
): void {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(QUERY_KEY, JSON.stringify({ range, filters }));
}

// ── Labels & tones ────────────────────────────────────────────────────────

export const SESSION_SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "managed", label: "Managed" },
  { value: "attached_tmux", label: "Attached tmux" },
  { value: "assistant", label: "Assistant" },
];

export const DRILLDOWN_KIND_OPTIONS: { value: TelemetryFactKind; label: string }[] = [
  { value: "session_lifecycle", label: "Session lifecycle" },
  { value: "turn", label: "Turns" },
  { value: "tool_call", label: "Tool calls" },
  { value: "context_snapshot", label: "Context snapshots" },
  { value: "limit_snapshot", label: "Limit snapshots" },
];

export const TOKEN_GROUP_BY_OPTIONS: { value: TokenGroupBy; label: string }[] = [
  { value: "time", label: "Over time" },
  { value: "backend", label: "By backend" },
  { value: "model", label: "By model" },
  { value: "repo", label: "By repo" },
  { value: "session", label: "By session" },
];

const TRANSITION_LABELS: Record<string, string> = {
  created: "Created",
  starting: "Starting",
  running: "Running",
  idle: "Idle",
  waiting: "Waiting on input",
  interrupted: "Interrupted",
  exited: "Exited",
  error: "Errored",
};

export function transitionLabel(transition: LifecycleTransition | string): string {
  return TRANSITION_LABELS[transition] ?? transition;
}

export function turnKindLabel(kind: TurnKind | string): string {
  return kind === "agent" ? "Agent turn" : kind === "user" ? "User turn" : kind;
}

export function factKindLabel(kind: TelemetryFactKind | string): string {
  return DRILLDOWN_KIND_OPTIONS.find((option) => option.value === kind)?.label ?? kind;
}

export type ToneWithNeutral = UsageTone | "neutral";

const TOOL_OUTCOME_LABELS: Record<string, string> = {
  succeeded: "Succeeded",
  failed: "Failed",
  cancelled: "Cancelled",
  timed_out: "Timed out",
  unknown: "Unknown",
};

export function toolOutcomeLabel(outcome: string | null | undefined): string {
  if (!outcome) return "Unknown";
  return TOOL_OUTCOME_LABELS[outcome] ?? outcome;
}

export function toolOutcomeTone(outcome: string | null | undefined): ToneWithNeutral {
  switch (outcome) {
    case "succeeded":
      return "good";
    case "failed":
    case "timed_out":
      return "danger";
    case "cancelled":
      return "warn";
    default:
      return "neutral";
  }
}

export function insightSeverityTone(severity: InsightSeverity): UsageTone {
  if (severity === "critical") return "danger";
  if (severity === "warning") return "warn";
  return "good";
}

const COVERAGE_LABELS: Record<TokenCoverage, string> = {
  entire: "Full history",
  tracked_since: "Tracked since backfill",
  partial: "Partial — some sessions untracked",
};

export function coverageLabel(coverage: TokenCoverage): string {
  return COVERAGE_LABELS[coverage] ?? coverage;
}

// Category keys a token totals map may carry — order here is the fixed
// stacking/legend order (never resorted by value; see the telemetry CSS
// section's `--tm-series-*` categorical slots). Mirrors the backend's 5
// unified buckets (`telemetry/tokens.py`) that every aggregate response's
// `totals` now carries.
export const TOKEN_CATEGORY_ORDER = UNIFIED_TOKEN_CATEGORIES;

export function tokenCategoryLabel(category: string): string {
  const known = UNIFIED_TOKEN_LABELS[category as UnifiedTokenCategory];
  if (known) return known;
  return category
    .split("_")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

export function orderedTokenCategories(totals: Record<string, number>): string[] {
  const known = TOKEN_CATEGORY_ORDER.filter((key) => key in totals);
  const rest = Object.keys(totals)
    .filter((key) => !TOKEN_CATEGORY_ORDER.includes(key as (typeof TOKEN_CATEGORY_ORDER)[number]))
    .sort();
  return [...known, ...rest];
}

// The two spend tiers the Tokens card and legend group by: fresh work the
// model actually did this range vs. cheap prior context re-sent to it. Keeping
// `cache_read` in its own tier is what stops it visually drowning the rest.
export const TOKEN_TIER_NEW_WORK: readonly UnifiedTokenCategory[] = [
  "fresh_input",
  "output",
  "reasoning",
  "cache_write",
];
export const TOKEN_TIER_REREAD: readonly UnifiedTokenCategory[] = ["cache_read"];

// Stacking/legend order: the four new-work buckets first (most→least
// characteristic), then the muted re-read band last so it reads as the
// backdrop the vivid work sits against.
export const TOKEN_DISPLAY_ORDER: readonly UnifiedTokenCategory[] = [
  ...TOKEN_TIER_NEW_WORK,
  ...TOKEN_TIER_REREAD,
];

const TOKEN_CATEGORY_COLORS: Record<UnifiedTokenCategory, string> = {
  fresh_input: "var(--tm-series-1)",
  output: "var(--tm-series-2)",
  reasoning: "var(--tm-series-3)",
  cache_write: "var(--tm-series-5)",
  cache_read: "var(--tm-token-reread)",
};

export function tokenCategoryColor(category: string): string {
  return TOKEN_CATEGORY_COLORS[category as UnifiedTokenCategory] ?? "var(--tm-series-6)";
}

// ── Instance health & capacity categories ────────────────────────────────
// Fixed accounting order (mirrors the backend StorageCategory enum) — also the
// stacked-bar order. Colors draw from the shared categorical series slots.
export const INSTANCE_CATEGORY_ORDER: readonly InstanceStorageCategory[] = [
  "database",
  "sqlite_companions",
  "live_sessions",
  "orphan_sessions",
  "attachments",
  "unclassified",
];

const INSTANCE_CATEGORY_LABELS: Record<InstanceStorageCategory, string> = {
  database: "Database",
  sqlite_companions: "SQLite companions",
  live_sessions: "Live sessions",
  orphan_sessions: "Orphan sessions",
  attachments: "Attachments",
  unclassified: "Unclassified",
};

const INSTANCE_CATEGORY_COLORS: Record<InstanceStorageCategory, string> = {
  database: "var(--tm-series-1)",
  sqlite_companions: "var(--tm-series-2)",
  live_sessions: "var(--tm-series-3)",
  orphan_sessions: "var(--tm-series-4)",
  attachments: "var(--tm-series-5)",
  unclassified: "var(--tm-series-6)",
};

export function instanceCategoryLabel(category: string): string {
  return (
    INSTANCE_CATEGORY_LABELS[category as InstanceStorageCategory] ??
    category
      .split("_")
      .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
      .join(" ")
  );
}

export function instanceCategoryColor(category: string): string {
  return INSTANCE_CATEGORY_COLORS[category as InstanceStorageCategory] ?? "var(--tm-series-6)";
}

// ── Database content breakdown ───────────────────────────────────────────
//
// The instance snapshot already carries `counts.table_rows` (a per-table
// COUNT(*)) and `counts.events_by_kind`. These derive a bounded,
// presentation-only view of what the SQLite file holds. Record counts are NOT
// bytes — they explain database contents without dividing the file size. Only
// the allowlisted user-facing tables are named; every other returned table
// folds into a single generic remainder so schema additions stay aggregate.

const DATABASE_CONTENT_GROUPS = {
  session: ["sessions", "events", "session_token_usage_records"],
  telemetry: [
    "telemetry_facts",
    "telemetry_fact_tag",
    "telemetry_daily_rollup",
    "telemetry_insight_dismissal",
    "telemetry_instance_snapshot",
  ],
} as const;

const DATABASE_CONTENT_LABELS: Record<string, string> = {
  sessions: "Session records",
  events: "Session events",
  session_token_usage_records: "Usage records",
  telemetry_facts: "Telemetry facts",
  telemetry_fact_tag: "Tag records",
  telemetry_daily_rollup: "Daily rollups",
  telemetry_insight_dismissal: "Dismissed insights",
  telemetry_instance_snapshot: "Saved instance snapshots",
};

export interface DatabaseCountRow {
  key: string;
  label: string;
  count: number;
}

export interface DatabaseEventKind {
  kind: string;
  label: string;
  count: number;
}

export interface DatabaseContentModel {
  session: DatabaseCountRow[];
  telemetry: DatabaseCountRow[];
  otherManagedRecords: number | null;
  eventMix: DatabaseEventKind[];
  hasContent: boolean;
}

function isValidCount(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value) && value >= 0;
}

function collectGroup(
  tableRows: Record<string, number>,
  keys: readonly string[],
): DatabaseCountRow[] {
  const rows: DatabaseCountRow[] = [];
  for (const key of keys) {
    if (!(key in tableRows)) continue;
    const count = tableRows[key];
    if (!isValidCount(count)) continue;
    rows.push({ key, label: DATABASE_CONTENT_LABELS[key] ?? key, count });
  }
  return rows;
}

function titleCaseKind(kind: string): string {
  const words = kind
    .split(/[^A-Za-z0-9]+/)
    .filter((part) => part.length > 0)
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1));
  return words.length > 0 ? words.join(" ") : kind;
}

export function deriveDatabaseContent(
  tableRows: Record<string, number>,
  eventsByKind: Record<string, number>,
): DatabaseContentModel {
  const session = collectGroup(tableRows, DATABASE_CONTENT_GROUPS.session);
  const telemetry = collectGroup(tableRows, DATABASE_CONTENT_GROUPS.telemetry);

  const claimed = new Set<string>([
    ...DATABASE_CONTENT_GROUPS.session,
    ...DATABASE_CONTENT_GROUPS.telemetry,
  ]);
  let otherSum = 0;
  let otherSeen = false;
  for (const [key, count] of Object.entries(tableRows)) {
    if (claimed.has(key) || !isValidCount(count)) continue;
    otherSum += count;
    otherSeen = true;
  }

  const eventMix: DatabaseEventKind[] = Object.entries(eventsByKind)
    .filter(([, count]) => isValidCount(count))
    .map(([kind, count]) => ({ kind, label: titleCaseKind(kind), count }))
    .sort((a, b) => b.count - a.count || a.label.localeCompare(b.label));

  return {
    session,
    telemetry,
    otherManagedRecords: otherSeen ? otherSum : null,
    eventMix,
    hasContent:
      session.length > 0 || telemetry.length > 0 || otherSeen || eventMix.length > 0,
  };
}

export interface TokenTierSplit {
  newWork: number;
  reread: number;
  total: number;
}

function tierSum(totals: Record<string, number>, keys: readonly string[]): number {
  return keys.reduce((sum, key) => sum + (totals[key] ?? 0), 0);
}

export function splitTokenTiers(totals: Record<string, number>): TokenTierSplit {
  const newWork = tierSum(totals, TOKEN_TIER_NEW_WORK);
  const reread = tierSum(totals, TOKEN_TIER_REREAD);
  return { newWork, reread, total: newWork + reread };
}

// Python's `datetime.weekday()` (Monday=0…Sunday=6) — matches
// `telemetry/aggregate.py`'s heatmap bucketing exactly.
export const DOW_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];

export function formatDayLabel(day: string): string {
  const parsed = new Date(`${day}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return day;
  return parsed.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export function formatHourLabel(hour: number): string {
  if (hour === 0) return "12a";
  if (hour === 12) return "12p";
  return hour < 12 ? `${hour}a` : `${hour - 12}p`;
}

const COMPACT_NUMBER_FORMATTER = new Intl.NumberFormat("en-US", {
  notation: "compact",
  maximumFractionDigits: 1,
});

export function formatCompactNumber(value: number): string {
  return COMPACT_NUMBER_FORMATTER.format(value);
}

const BYTE_UNITS = ["B", "KiB", "MiB", "GiB", "TiB", "PiB"] as const;

// Human-readable IEC byte size (mirrors the backend's format_bytes). The exact
// value is kept for an accessible title/aria-label so screen readers and
// hover both see the precise number.
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes <= 0) return "0 B";
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < BYTE_UNITS.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded = unit === 0 ? Math.round(value) : value.toFixed(1);
  return `${rounded} ${BYTE_UNITS[unit]}`;
}

export function formatExactBytes(bytes: number): string {
  return `${bytes.toLocaleString("en-US")} bytes`;
}

export function confidenceLabel(confidence: string): string {
  return confidence.charAt(0).toUpperCase() + confidence.slice(1);
}

// The effective range a response echoed back (CONTRACT.md §4) — always
// rendered instead of the client's own guess at what it asked for.
export function formatRangeLabel(range: TelemetryRange): string {
  const start = new Date(range.start);
  const end = new Date(range.end);
  const fmt: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  // With the host-tz offset from the range echo, shift each instant by it and
  // render in UTC so the day matches the host-tz calendar day the range covers.
  // Without a usable offset (older payload / NaN), fall back to the browser tz.
  const offset = range.utc_offset_minutes;
  const shiftMinutes = typeof offset === "number" && !Number.isNaN(offset) ? offset : null;
  const fmtDay = (instant: Date): string =>
    shiftMinutes !== null
      ? new Date(instant.getTime() + shiftMinutes * 60_000).toLocaleDateString(undefined, {
          ...fmt,
          timeZone: "UTC",
        })
      : instant.toLocaleDateString(undefined, fmt);
  const startLabel = Number.isNaN(start.getTime()) ? range.start : fmtDay(start);
  // `end` is exclusive; show the inclusive last day for a human-readable range.
  const inclusiveEnd = Number.isNaN(end.getTime()) ? end : new Date(end.getTime() - 1000);
  const endLabel = Number.isNaN(end.getTime()) ? range.end : fmtDay(inclusiveEnd);
  return `${startLabel} – ${endLabel} (${range.tz})`;
}

// A session id is `<backend>-<8 hex>` (e.g. `claude_code-196bdf37`) — the
// distinguishing part is the *suffix*, so a plain `.slice(0, N)` truncation
// collapses every row from the same backend to an identical prefix. Show the
// full id when it already fits; otherwise keep both ends so the id stays
// distinguishable at a glance.
const SHORT_ID_MAX = 24;
const SHORT_ID_HEAD = 12;
const SHORT_ID_TAIL = 8;

export function shortId(id: string): string {
  if (id.length <= SHORT_ID_MAX) return id;
  return `${id.slice(0, SHORT_ID_HEAD)}…${id.slice(-SHORT_ID_TAIL)}`;
}
