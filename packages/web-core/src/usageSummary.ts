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

// "1h 12m", "42m", "3d 4h": how long until a window resets; null once it has, or
// when the time is unknown.
export function formatResetsIn(iso: string | null | undefined, now: number = Date.now()): string | null {
  if (!iso) return null;
  const target = Date.parse(iso);
  if (!Number.isFinite(target) || target <= now) return null;
  const minutes = Math.round((target - now) / 60000);
  if (minutes < 60) return `${Math.max(minutes, 1)}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return minutes % 60 ? `${hours}h ${minutes % 60}m` : `${hours}h`;
  const days = Math.floor(hours / 24);
  return hours % 24 ? `${days}d ${hours % 24}h` : `${days}d`;
}

export interface WindowReading {
  kind: UsageWindowKind;
  label: "5H" | "WK";
  percent: number;
  tone: UsageTone;
  // "1h 12m", or the backend's own description when it gives no reset time.
  resetsIn: string | null;
}

function readWindow(kind: UsageWindowKind, window: UsageWindow | null, now: number): WindowReading | null {
  const percent = window ? rateLimitWindowPercent(window) : null;
  if (!window || percent === null) return null;
  return {
    kind,
    label: kind === "5h" ? "5H" : "WK",
    percent,
    tone: usageTone(percent),
    resetsIn: formatResetsIn(window.resets_at, now) ?? window.reset_description ?? null,
  };
}

// An account's 5-hour and weekly windows, whichever it reports.
export function accountWindows(bucket: UsageDashboardBucket, now: number = Date.now()): WindowReading[] {
  return (["5h", "weekly"] as const)
    .map((kind) => readWindow(kind, findUsageWindow(bucket, kind), now))
    .filter((reading): reading is WindowReading => reading !== null);
}

export interface ConstrainedWindow extends WindowReading {
  accountLabel: string;
}

// The window closest to its limit across every account; the 5-hour window wins a tie.
export function mostConstrainedWindow(
  buckets: UsageDashboardBucket[],
  now: number = Date.now(),
): ConstrainedWindow | null {
  let best: ConstrainedWindow | null = null;
  for (const bucket of buckets) {
    for (const reading of accountWindows(bucket, now)) {
      if (best === null || reading.percent > best.percent) {
        best = { ...reading, accountLabel: bucket.account_label };
      }
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
