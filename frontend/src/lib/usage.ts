import { SessionRateLimitUsage, UsageWindow } from "@/lib/types";

const TOKEN_FORMATTER = new Intl.NumberFormat("en-US");
const RELATIVE_TIME_FORMATTER = new Intl.RelativeTimeFormat("en", {
  numeric: "auto",
});

export type UsageTone = "good" | "warn" | "danger";

export function formatTokens(value: number): string {
  return TOKEN_FORMATTER.format(value);
}

type RelativeTimeUnit = "second" | "minute" | "hour" | "day";

// Signed magnitude + unit for a timestamp relative to now, so the verbose and
// terse renderings below stay on the same buckets and can never disagree by a unit.
function relativeTimeParts(
  value: string,
): { value: number; unit: RelativeTimeUnit } | null {
  const timestamp = Date.parse(value);
  if (!Number.isFinite(timestamp)) {
    return null;
  }
  const diffSeconds = Math.round((timestamp - Date.now()) / 1000);
  const absSeconds = Math.abs(diffSeconds);
  if (absSeconds < 60) {
    return { value: diffSeconds, unit: "second" };
  }
  if (absSeconds < 3600) {
    return { value: Math.round(diffSeconds / 60), unit: "minute" };
  }
  if (absSeconds < 86400) {
    return { value: Math.round(diffSeconds / 3600), unit: "hour" };
  }
  return { value: Math.round(diffSeconds / 86400), unit: "day" };
}

export function formatRelativeTime(value: string): string {
  const parts = relativeTimeParts(value);
  if (!parts) {
    return "Unknown";
  }
  return RELATIVE_TIME_FORMATTER.format(parts.value, parts.unit);
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

const RELATIVE_TIME_SUFFIX: Record<RelativeTimeUnit, string> = {
  second: "s",
  minute: "m",
  hour: "h",
  day: "d",
};

// Terse reset label ("in 4h", "3d ago", "now") for tight, glanceable surfaces
// where the verbose relative phrase would crowd out the meter. Keeps the "in"/"ago"
// direction so a stale, already-elapsed window is not mistaken for an upcoming one.
// Falls back to the backend's free-form description when no machine-readable reset
// time is available.
export function formatRateLimitWindowResetShort(window: UsageWindow): string | null {
  if (window.resets_at) {
    const parts = relativeTimeParts(window.resets_at);
    if (!parts) {
      return null;
    }
    if (parts.value === 0) {
      return "now";
    }
    const magnitude = `${Math.abs(parts.value)}${RELATIVE_TIME_SUFFIX[parts.unit]}`;
    return parts.value < 0 ? `${magnitude} ago` : `in ${magnitude}`;
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
