// Glanceable summaries of the data behind the home instrument tiles: account
// rate-limit windows, provider health, queued schedules and board channels.

import { humaniseBackend, type BackendCatalog } from "./backends";
import { isRecurring } from "./recurrence";
import type {
  BoardChannel,
  MessageSchedule,
  ProviderUsageStatus,
  ScheduledSession,
  UsageDashboardBucket,
  UsageWindow,
} from "./types";
import {
  formatRateLimitWindowReset,
  formatRateLimitWindowResetShort,
  rateLimitUsageTone,
  rateLimitWindowPercent,
  usageTone,
  type UsageTone,
} from "./usage";

// ─── Account windows ───

export type UsageWindowKind = "5h" | "weekly";

type WindowSource = { snapshot: { windows: UsageWindow[] } };

export function findUsageWindow(bucket: WindowSource, kind: UsageWindowKind): UsageWindow | null {
  for (const window of bucket.snapshot.windows) {
    const label = (window.label || "").toLowerCase();
    if (kind === "5h" && (label.includes("5h") || window.window_minutes === 300)) {
      return window;
    }
    if (
      kind === "weekly" &&
      (label.includes("week") || label.includes("7d") || window.window_minutes === 7 * 24 * 60)
    ) {
      return window;
    }
  }
  return null;
}

export function bucketPeak(bucket: WindowSource): number {
  return Math.max(0, ...bucket.snapshot.windows.map((window) => rateLimitWindowPercent(window) ?? 0));
}

// Worst-first, so the account nearest its limit reads first.
export function orderBucketsWorstFirst(buckets: UsageDashboardBucket[]): UsageDashboardBucket[] {
  return [...buckets].sort((a, b) => bucketPeak(b) - bucketPeak(a));
}

// The agent name for a session bucket, the provider label for a provider bucket
// (with a stale hint).
export function bucketSourceLabel(bucket: UsageDashboardBucket, catalog: BackendCatalog | null): string {
  if (bucket.origin === "provider") {
    return bucket.health.stale ? `${bucket.provider_label} · stale` : bucket.provider_label;
  }
  return humaniseBackend(bucket.backend, catalog ?? undefined);
}

export interface ConstrainedWindow {
  accountLabel: string;
  kind: UsageWindowKind;
  label: "5H" | "WK";
  percent: number;
  tone: UsageTone;
  // Terse ("in 4h") and full reset text, when the window reports one.
  resetShort: string | null;
  resetFull: string | null;
}

// The window closest to its limit across every account; the 5-hour window wins a tie.
export function mostConstrainedWindow(buckets: UsageDashboardBucket[]): ConstrainedWindow | null {
  let best: ConstrainedWindow | null = null;
  for (const bucket of buckets) {
    for (const kind of ["5h", "weekly"] as const) {
      const window = findUsageWindow(bucket, kind);
      const percent = window ? rateLimitWindowPercent(window) : null;
      if (!window || percent === null) continue;
      if (best !== null && percent <= best.percent) continue;
      best = {
        accountLabel: bucket.account_label,
        kind,
        label: kind === "5h" ? "5H" : "WK",
        percent,
        tone: usageTone(percent),
        resetShort: formatRateLimitWindowResetShort(window),
        resetFull: formatRateLimitWindowReset(window),
      };
    }
  }
  return best;
}

// ─── Provider health ───

export interface ProviderHealthLine {
  text: string;
  danger: boolean;
}

const PROVIDER_ERROR_LABELS: Record<string, ProviderHealthLine> = {
  missing_token: { text: "token missing", danger: true },
  identity_failed: { text: "identity failed", danger: true },
  network: { text: "unreachable", danger: true },
  unknown: { text: "error", danger: true },
  permission_denied: { text: "permission denied", danger: false },
  usage_unavailable: { text: "usage unavailable", danger: false },
  no_matching_usage: { text: "no usage", danger: false },
};

// The worst coarse error a provider reported, as one compact line.
export function providerHealthLine(status: ProviderUsageStatus): ProviderHealthLine | null {
  const errors = Object.entries(status.error_counts).filter(([, count]) => count > 0);
  if (errors.length === 0) return null;
  // A hard failure (danger) outranks a soft one.
  errors.sort(
    (a, b) =>
      Number(PROVIDER_ERROR_LABELS[b[0]]?.danger ?? true) -
      Number(PROVIDER_ERROR_LABELS[a[0]]?.danger ?? true),
  );
  const entry = PROVIDER_ERROR_LABELS[errors[0][0]] ?? { text: "error", danger: true };
  return { text: `${status.provider_label} · ${entry.text}`, danger: entry.danger };
}

// Health lines for providers that resolved no account bucket but reported an
// error, so a configured provider is visible even with no card.
export function unresolvedProviderHealth(
  buckets: UsageDashboardBucket[],
  providers: ProviderUsageStatus[],
): ProviderHealthLine[] {
  const resolved = new Set(
    buckets.filter((bucket) => bucket.origin === "provider").map((bucket) => bucket.provider_id),
  );
  return providers
    .filter((provider) => !resolved.has(provider.provider_id))
    .map(providerHealthLine)
    .filter((line): line is ProviderHealthLine => line !== null);
}

// The worst account window, escalated by provider errors.
export function overallUsageTone(
  buckets: UsageDashboardBucket[],
  health: ProviderHealthLine[],
): UsageTone {
  let tone: UsageTone = "good";
  for (const bucket of buckets) {
    const bucketTone = rateLimitUsageTone(bucket.snapshot);
    if (bucketTone === "danger") {
      tone = "danger";
      break;
    }
    if (bucketTone === "warn") tone = "warn";
  }
  if (health.some((line) => line.danger)) return "danger";
  if (tone !== "danger" && health.length > 0) return tone === "good" ? "warn" : tone;
  return tone;
}

// ─── Scheduled ───

export interface ScheduledItem {
  key: string;
  kind: "launch" | "message";
  when: number | null;
  whenLabel: string;
  target: string;
  preview: string;
  recurring: boolean;
}

export function formatShortWhen(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "queued";
  const target = Date.parse(iso);
  if (!Number.isFinite(target)) return "queued";
  const delta = target - now;
  if (delta <= 0) return "due";
  const minutes = Math.round(delta / 60000);
  if (minutes < 60) return `in ${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.floor(hours / 24)}d`;
}

export interface ScheduledSummary {
  queued: number;
  // The soonest first.
  items: ScheduledItem[];
  overflow: number;
}

export function summarizeScheduled(
  schedules: ScheduledSession[],
  messageSchedules: MessageSchedule[],
  titleById: ReadonlyMap<string, string>,
  limit = 3,
  now: number = Date.now(),
): ScheduledSummary {
  const pendingSessions = schedules.filter((schedule) => schedule.status === "pending");
  const pendingMessages = messageSchedules.filter((message) => message.status === "pending");
  const items: ScheduledItem[] = [
    ...pendingSessions.map((schedule) => ({
      key: `launch:${schedule.id}`,
      kind: "launch" as const,
      when: Date.parse(schedule.scheduled_at),
      whenLabel: formatShortWhen(schedule.scheduled_at, now),
      target: schedule.title?.trim() || `${schedule.backend} session`,
      preview: schedule.initial_prompt?.trim() || schedule.cwd,
      recurring: isRecurring(schedule),
    })),
    ...pendingMessages.map((message) => ({
      key: `message:${message.id}`,
      kind: "message" as const,
      when: message.scheduled_at ? Date.parse(message.scheduled_at) : null,
      whenLabel: formatShortWhen(message.scheduled_at, now),
      target: titleById.get(message.session_id)?.trim() || "session",
      preview: message.text?.trim() || "",
      recurring: isRecurring(message),
    })),
  ].sort((a, b) => (a.when ?? Infinity) - (b.when ?? Infinity));
  const queued = items.length;
  const shown = items.slice(0, limit);
  return { queued, items: shown, overflow: queued - shown.length };
}

// ─── Board channels ───

export interface ChannelSummary {
  channels: number;
  posts: number;
  lastActivity: string | null;
  // Most recently active first (the API's order).
  top: BoardChannel[];
}

export function summarizeChannels(channels: BoardChannel[], topCount = 3): ChannelSummary {
  const lastActivity =
    channels
      .map((channel) => channel.last_created_at)
      .filter(Boolean)
      .sort()
      .at(-1) ?? null;
  return {
    channels: channels.length,
    posts: channels.reduce((sum, channel) => sum + channel.entry_count, 0),
    lastActivity,
    top: channels.slice(0, topCount),
  };
}
