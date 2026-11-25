import { humaniseBackend, type BackendCatalog } from "../backends";
import { UNIFIED_TOKEN_LABELS, unifyTokens } from "../tokens";
import type { SessionContextUsage, SessionRecord, SessionTokenUsage } from "../types";
import { clampPercent, formatRelativeTime, formatTokens, rateLimitUsageTone, usageTone, type UsageTone } from "../usage";

// Versioned so a future shape change can coexist with an old saved value.
export const USAGE_POPOVER_SIZE_KEY = "waypoint.usage-popover-size.v1";

function contextUsagePercent(usage: SessionContextUsage): number | null {
  const windowTokens = usage.context_window_tokens;
  if (!windowTokens || windowTokens <= 0) {
    return null;
  }
  // A snapshot with no used tokens comes from an endpoint that reports only
  // output usage (no prompt/cache accounting), so the fill is unknown rather
  // than genuinely empty: no percentage, not 0%.
  if (usage.used_tokens <= 0) {
    return null;
  }
  return Math.round((usage.used_tokens / windowTokens) * 100);
}

export function sessionCoverageLabel(usage: SessionTokenUsage): string {
  switch (usage.coverage) {
    case "entire_waypoint_session":
      return "Entire Waypoint session";
    case "tracked_since":
      return `Tracked since ${formatRelativeTime(usage.observed_from)}`;
    default:
      return usage.coverage_note ?? "Partial coverage";
  }
}

function breakdownLabel(key: string): string {
  switch (key) {
    case "input_tokens":
      return "Input";
    case "cached_input_tokens":
      return "Cached input";
    case "output_tokens":
      return "Output";
    case "reasoning_output_tokens":
    case "reasoning_tokens":
      return "Reasoning";
    case "cache_read_tokens":
      return "Cache read";
    case "cache_creation_tokens":
    case "cache_write_tokens":
      return "Cache write";
    default:
      return key.replaceAll("_", " ");
  }
}

export interface UsagePanelModel {
  contextUsage: SessionContextUsage | null;
  tokenUsage: SessionTokenUsage | null;
  rateLimitUsage: SessionRecord["rate_limit_usage"] | null;
  // Whether there is anything to open a panel for.
  hasUsage: boolean;
  // The clamped context fill, or null when it is unknown.
  contextPercent: number | null;
  contextTone: UsageTone;
  contextWindowTokens: number | null;
  // The endpoint reported a turn but no prompt or cache tokens.
  contextUnreported: boolean;
  // The last turn's nonzero token categories.
  contextBreakdown: [label: string, tokens: number][];
  rateLimitSourceLabel: string;
  // The more urgent of context pressure and rate-limit pressure.
  tone: UsageTone;
  // Totals over disjoint categories, labelled.
  tokenTotals: [label: string, tokens: number][];
  hasTokenTotals: boolean;
  // False when nothing is tracked yet and only the coverage line is shown.
  hasTrackedTurns: boolean;
  // The context and rate limits in words, for the control that opens the panel.
  summary: string[];
}

function worse(a: UsageTone, b: UsageTone): UsageTone {
  if (a === "danger" || b === "danger") return "danger";
  if (a === "warn" || b === "warn") return "warn";
  return "good";
}

export function usagePanelModel(session: SessionRecord | null, catalog?: BackendCatalog): UsagePanelModel {
  const contextUsage = session?.context_usage ?? null;
  const tokenUsage = session?.session_token_usage ?? null;
  const rateLimitUsage = session?.rate_limit_usage ?? null;

  const percent = contextUsage ? contextUsagePercent(contextUsage) : null;
  const contextPercent = clampPercent(percent);
  const contextTone = usageTone(percent);
  const windowTokens = contextUsage?.context_window_tokens ?? 0;
  const contextWindowTokens = windowTokens > 0 ? windowTokens : null;
  const contextUnreported = contextUsage !== null && contextUsage.used_tokens <= 0;

  const contextSummary = contextUsage
    ? contextUnreported
      ? contextWindowTokens !== null
        ? `${formatTokens(contextWindowTokens)} window · usage not reported`
        : "usage not reported"
      : contextWindowTokens !== null && contextPercent !== null
        ? `${formatTokens(contextUsage.used_tokens)} / ${formatTokens(contextWindowTokens)} (${contextPercent}%)`
        : formatTokens(contextUsage.used_tokens)
    : null;

  const rateLimitSummary = rateLimitUsage
    ? rateLimitUsage.windows.length > 0
      ? rateLimitUsage.windows
          .map((entry) => `${entry.label} ${Math.round(entry.used_percent)}%`)
          .join(" · ")
      : rateLimitUsage.notes?.length
        ? rateLimitUsage.notes.join(" · ")
        : null
    : null;

  const rateLimitSourceLabel = rateLimitUsage
    ? rateLimitUsage.origin === "usage_provider"
      ? // Provider projections carry a server-owned display label; flag an
        // unavailable selection so the readout isn't mistaken for live.
        (rateLimitUsage.unavailable
          ? `${rateLimitUsage.source_label ?? "Usage provider"} (unavailable)`
          : rateLimitUsage.source_label) ?? "Usage provider"
      : rateLimitUsage.notes?.length
        ? rateLimitUsage.notes.join(" · ")
        : humaniseBackend(rateLimitUsage.source, catalog)
    : "Unavailable";

  const tokenTotals: [string, number][] = tokenUsage
    ? Object.entries(unifyTokens(tokenUsage.source, tokenUsage.totals ?? {})).map(([key, value]) => [
        UNIFIED_TOKEN_LABELS[key as keyof typeof UNIFIED_TOKEN_LABELS] ?? key,
        value,
      ])
    : [];

  return {
    contextUsage,
    tokenUsage,
    rateLimitUsage,
    hasUsage: contextUsage !== null || tokenUsage !== null || rateLimitUsage !== null,
    contextPercent,
    contextTone,
    contextWindowTokens,
    contextUnreported,
    contextBreakdown: contextUsage
      ? Object.entries(contextUsage.breakdown ?? {})
          .filter(([, value]) => value > 0)
          .map(([key, value]) => [breakdownLabel(key), value])
      : [],
    rateLimitSourceLabel,
    tone: worse(contextTone, rateLimitUsageTone(rateLimitUsage)),
    tokenTotals,
    hasTokenTotals: tokenUsage !== null && Object.keys(tokenUsage.totals ?? {}).length > 0,
    hasTrackedTurns: tokenUsage !== null && tokenUsage.tracked_turns > 0,
    summary: [
      contextSummary ? `Current context ${contextSummary}` : null,
      rateLimitSummary ? `Rate limits ${rateLimitSummary}` : null,
    ].filter((part) => part !== null),
  };
}
