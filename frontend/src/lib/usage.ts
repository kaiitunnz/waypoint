import { SessionRateLimitUsage, UsageWindow } from "@/lib/types";

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US");
const RELATIVE_TIME_FORMATTER = new Intl.RelativeTimeFormat("en", {
  numeric: "auto",
});

export type UsageTone = "good" | "warn" | "danger";

export function formatTokens(value: number): string {
  return TOKEN_FORMATTER.format(value);
}

export function formatRelativeTime(value: string): string {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return "Unknown";
  }
  const diffSeconds = Math.round((timestamp - Date.now()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  if (absSeconds < 60) {
    return RELATIVE_TIME_FORMATTER.format(diffSeconds, "second");
  }
  if (absSeconds < 3600) {
    return RELATIVE_TIME_FORMATTER.format(Math.round(diffSeconds / 60), "minute");
  }
  if (absSeconds < 86400) {
    return RELATIVE_TIME_FORMATTER.format(Math.round(diffSeconds / 3600), "hour");
  }
  return RELATIVE_TIME_FORMATTER.format(Math.round(diffSeconds / 86400), "day");
}

export function clampPercent(percent: number | null): number | null {
  if (percent === null) {
    return null;
  }
  return Math.min(100, Math.max(0, percent));
}

export function usageTone(percent: number | null): UsageTone {
  if (percent === null) {
    return "good";
  }
  if (percent >= 90) {
    return "danger";
  }
  if (percent >= 70) {
    return "warn";
  }
  return "good";
}

export function rateLimitWindowPercent(window: UsageWindow): number | null {
  if (window.used_percent < 0 || !Number.isFinite(window.used_percent)) {
    return null;
  }
  return clampPercent(Math.round(window.used_percent));
}

export function formatRateLimitWindowTokens(window: UsageWindow): string | null {
  const parts: string[] = [];
  if (window.used_tokens !== undefined && window.used_tokens !== null) {
    parts.push(`${formatTokens(window.used_tokens)} used`);
  }
  if (window.remaining_tokens !== undefined && window.remaining_tokens !== null) {
    parts.push(`${formatTokens(window.remaining_tokens)} left`);
  }
  if (
    window.limit_tokens !== undefined &&
    window.limit_tokens !== null &&
    window.used_tokens !== undefined &&
    window.used_tokens !== null
  ) {
    parts.push(`of ${formatTokens(window.limit_tokens)}`);
  }
  return parts.length > 0 ? parts.join(" · ") : null;
}

export function formatRateLimitWindowReset(window: UsageWindow): string | null {
  if (window.resets_at) {
    return formatRelativeTime(window.resets_at);
  }
  return window.reset_description ?? null;
}

export function rateLimitUsageTone(
  usage: SessionRateLimitUsage | null,
): UsageTone {
  if (!usage || usage.windows.length === 0) {
    return "good";
  }
  const worst = Math.max(
    ...usage.windows.map((window) => rateLimitWindowPercent(window) ?? 0),
  );
  return usageTone(worst);
}
