"use client";

import {
  InsightSeverity,
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
// section's `--tm-series-*` categorical slots).
export const TOKEN_CATEGORY_ORDER = [
  "input",
  "output",
  "cache_read",
  "cache_write",
  "reasoning",
] as const;

export function tokenCategoryLabel(category: string): string {
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

// The effective range a response echoed back (CONTRACT.md §4) — always
// rendered instead of the client's own guess at what it asked for.
export function formatRangeLabel(range: TelemetryRange): string {
  const start = new Date(range.start);
  const end = new Date(range.end);
  const fmt: Intl.DateTimeFormatOptions = { month: "short", day: "numeric" };
  const startLabel = Number.isNaN(start.getTime()) ? range.start : start.toLocaleDateString(undefined, fmt);
  // `end` is exclusive; show the inclusive last day for a human-readable range.
  const inclusiveEnd = Number.isNaN(end.getTime()) ? end : new Date(end.getTime() - 1000);
  const endLabel = Number.isNaN(end.getTime())
    ? range.end
    : inclusiveEnd.toLocaleDateString(undefined, fmt);
  return `${startLabel} – ${endLabel} (${range.tz})`;
}
