"use client";

import Link from "next/link";

import {
  formatRateLimitWindowReset,
  formatRateLimitWindowResetShort,
  formatRelativeTime,
  rateLimitUsageTone,
  rateLimitWindowPercent,
  usageTone,
  type UsageTone,
} from "@/lib/usage";
import { isRecurring } from "@/lib/recurrence";
import { humaniseBackend, type BackendCatalog } from "@/lib/backends";
import type {
  BoardChannel,
  MessageSchedule,
  ProviderUsageStatus,
  ScheduledSession,
  SessionRecord,
  UsageDashboardBucket,
  UsageWindow,
} from "@/lib/types";

// Right-rail instrument tiles: compact, glanceable status headlines that link
// to (or open) the dedicated surfaces. They consume already-fetched data
// (Board, Scheduled) or one existing usage fetch (Telemetry) and never mount a
// full dashboard or add polling.

interface InstrumentRailProps {
  usageBuckets: UsageDashboardBucket[] | null;
  usageProviders: ProviderUsageStatus[];
  backendCatalog: BackendCatalog | null;
  refreshingUsage: boolean;
  onRefreshUsage: () => void;
  telemetryEnabled: boolean;
  boardChannels: BoardChannel[];
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  sessions: SessionRecord[];
  onOpenScheduled: () => void;
}

export function InstrumentRail({
  usageBuckets,
  usageProviders,
  backendCatalog,
  refreshingUsage,
  onRefreshUsage,
  telemetryEnabled,
  boardChannels,
  schedules,
  messageSchedules,
  sessions,
  onOpenScheduled,
}: InstrumentRailProps) {
  return (
    <aside className="rail" aria-label="Instruments">
      <TelemetryTile
        buckets={usageBuckets}
        providers={usageProviders}
        catalog={backendCatalog}
        telemetryEnabled={telemetryEnabled}
        refreshing={refreshingUsage}
        onRefresh={onRefreshUsage}
      />
      <ScheduledTile
        schedules={schedules}
        messageSchedules={messageSchedules}
        sessions={sessions}
        onOpen={onOpenScheduled}
      />
      <BoardTile channels={boardChannels} />
    </aside>
  );
}

/* ── Telemetry ── */

function findWindow(
  bucket: { snapshot: { windows: UsageWindow[] } },
  kind: "5h" | "weekly",
): UsageWindow | null {
  for (const window of bucket.snapshot.windows) {
    const label = (window.label || "").toLowerCase();
    if (kind === "5h" && (label.includes("5h") || window.window_minutes === 300)) {
      return window;
    }
    if (
      kind === "weekly" &&
      (label.includes("week") ||
        label.includes("7d") ||
        window.window_minutes === 7 * 24 * 60)
    ) {
      return window;
    }
  }
  return null;
}

function bucketPeak(bucket: { snapshot: { windows: UsageWindow[] } }): number {
  return Math.max(
    0,
    ...bucket.snapshot.windows.map((w) => rateLimitWindowPercent(w) ?? 0),
  );
}

// Source label for an account row: the agent name for a session bucket, the
// provider label for a configured-provider bucket (with a stale hint).
function sourceLabel(
  bucket: UsageDashboardBucket,
  catalog: BackendCatalog | null,
): string {
  if (bucket.origin === "provider") {
    return bucket.health.stale
      ? `${bucket.provider_label} · stale`
      : bucket.provider_label;
  }
  return humaniseBackend(bucket.backend, catalog ?? undefined);
}

// A provider whose latest refresh resolved no account bucket but reported a
// coarse error surfaces as a compact health line so missing_token /
// identity_failed states are visible without a card.
const PROVIDER_ERROR_LABELS: Record<string, { text: string; danger: boolean }> = {
  missing_token: { text: "token missing", danger: true },
  identity_failed: { text: "identity failed", danger: true },
  network: { text: "unreachable", danger: true },
  unknown: { text: "error", danger: true },
  permission_denied: { text: "permission denied", danger: false },
  usage_unavailable: { text: "usage unavailable", danger: false },
  no_matching_usage: { text: "no usage", danger: false },
};

function providerHealthLine(
  status: ProviderUsageStatus,
): { text: string; danger: boolean } | null {
  const errors = Object.entries(status.error_counts).filter(([, n]) => n > 0);
  if (errors.length === 0) return null;
  // Worst error first: a hard failure (danger) outranks a soft one.
  errors.sort(
    (a, b) =>
      Number(PROVIDER_ERROR_LABELS[b[0]]?.danger ?? true) -
      Number(PROVIDER_ERROR_LABELS[a[0]]?.danger ?? true),
  );
  const [code] = errors[0];
  const entry = PROVIDER_ERROR_LABELS[code] ?? { text: "error", danger: true };
  return { text: `${status.provider_label} · ${entry.text}`, danger: entry.danger };
}

function TelemetryTile({
  buckets,
  providers,
  catalog,
  telemetryEnabled,
  refreshing,
  onRefresh,
}: {
  buckets: UsageDashboardBucket[] | null;
  providers: ProviderUsageStatus[];
  catalog: BackendCatalog | null;
  telemetryEnabled: boolean;
  refreshing: boolean;
  onRefresh: () => void;
}) {
  // Provider health lines for providers that resolved no bucket but reported an
  // error — so a configured Lumid provider is visible even with no card.
  const bucketProviderIds = new Set(
    (buckets ?? [])
      .filter((b) => b.origin === "provider")
      .map((b) => b.provider_id),
  );
  const providerHealth = providers
    .filter((p) => !bucketProviderIds.has(p.provider_id))
    .map((p) => providerHealthLine(p))
    .filter((line): line is { text: string; danger: boolean } => line !== null);

  // Degrade to a plain link only when there is no usage data at all: no account
  // buckets and no provider health worth showing. Lamp keyed off the master
  // telemetry opt-in rather than live figures.
  const hasBuckets = buckets !== null && buckets.length > 0;
  if (!hasBuckets && providerHealth.length === 0) {
    return (
      <Link className="inst" href="/telemetry">
        <div className="inst-top">
          <span className="inst-name">Telemetry</span>
          <span className="inst-go" aria-hidden="true">
            →
          </span>
        </div>
        <span className={`inst-lamp tone-${telemetryEnabled ? "info" : "off"}`}>
          {buckets === null ? "Standing by" : "No accounts"}
        </span>
        <h4 className="inst-headline">Telemetry</h4>
        <p className="inst-line">Usage &amp; rate limits</p>
      </Link>
    );
  }

  const accounts = buckets ?? [];
  let tone: UsageTone = "good";
  for (const bucket of accounts) {
    const t = rateLimitUsageTone(bucket.snapshot);
    if (t === "danger") {
      tone = "danger";
      break;
    }
    if (t === "warn") tone = "warn";
  }
  // A hard provider failure (danger) escalates the tile lamp even with no card.
  if (providerHealth.some((h) => h.danger)) tone = "danger";
  else if (tone !== "danger" && providerHealth.length > 0) {
    tone = tone === "good" ? "warn" : tone;
  }
  const lampTone = tone === "good" ? "ok" : tone === "warn" ? "warn" : "danger";
  const lampText =
    tone === "good"
      ? "All clear"
      : tone === "warn"
        ? "Approaching limit"
        : "Quota critical";

  // Worst-first so the account nearest its limit reads first at a glance.
  const ordered = [...accounts].sort((a, b) => bucketPeak(b) - bucketPeak(a));
  const headline =
    accounts.length > 0
      ? `${accounts.length} account${accounts.length === 1 ? "" : "s"}`
      : "Providers";

  return (
    <section className="inst">
      <Link className="inst-top inst-top-link" href="/telemetry">
        <span className="inst-name">Telemetry</span>
        <span className="inst-go" aria-hidden="true">
          →
        </span>
      </Link>
      <div className="inst-lamp-row">
        <span className={`inst-lamp tone-${lampTone}`}>{lampText}</span>
        <button
          type="button"
          className={`inst-refresh${refreshing ? " is-spinning" : ""}`}
          onClick={onRefresh}
          disabled={refreshing}
          aria-busy={refreshing}
          aria-label={refreshing ? "Refreshing usage" : "Refresh usage"}
          title="Refresh usage"
        >
          <RefreshGlyph />
        </button>
      </div>
      <h4 className="inst-headline">{headline}</h4>
      {ordered.length > 0 ? (
        <ul className="inst-accounts">
          {ordered.map((bucket) => (
            <li className="inst-account" key={bucket.account_key}>
              <span className="inst-account-head">
                <span
                  className={`inst-account-dot tone-${toneOf(bucketPeak(bucket))}`}
                  aria-hidden="true"
                />
                <span className="inst-account-name" title={bucket.account_label}>
                  {bucket.account_label}
                </span>
                <span className="inst-account-src" title={sourceLabel(bucket, catalog)}>
                  {sourceLabel(bucket, catalog)}
                </span>
              </span>
              <UsageWindowMeter label="5h" window={findWindow(bucket, "5h")} />
              <UsageWindowMeter label="wk" window={findWindow(bucket, "weekly")} />
            </li>
          ))}
        </ul>
      ) : null}
      {providerHealth.length > 0 ? (
        <ul className="inst-provider-health-list">
          {providerHealth.map((line) => (
            <li className="inst-provider-health" key={line.text}>
              <span
                className={`inst-account-dot tone-${line.danger ? "danger" : "warn"}`}
                aria-hidden="true"
              />
              <span className="inst-provider-health-text">{line.text}</span>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}

function RefreshGlyph() {
  return (
    <svg
      className="inst-refresh-glyph"
      viewBox="0 0 24 24"
      width="14"
      height="14"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M21 12a9 9 0 1 1-2.64-6.36" />
      <path d="M21 3v6h-6" />
    </svg>
  );
}

function toneOf(percent: number | null): "ok" | "warn" | "danger" {
  const t = usageTone(percent);
  return t === "good" ? "ok" : t === "warn" ? "warn" : "danger";
}

function UsageWindowMeter({
  label,
  window,
}: {
  label: string;
  window: UsageWindow | null;
}) {
  const percent = window ? rateLimitWindowPercent(window) : null;
  const reset = window ? formatRateLimitWindowResetShort(window) : null;
  const resetTitle = window ? formatRateLimitWindowReset(window) : null;
  const tone = toneOf(percent);
  return (
    <span className="inst-window">
      <span className="inst-window-cap">{label}</span>
      <span className="inst-window-meter" aria-hidden="true">
        <span
          className={`inst-window-bar tone-${tone}`}
          style={{ width: `${Math.min(percent ?? 0, 100)}%` }}
        />
      </span>
      <span className="inst-window-pct">
        {percent !== null ? `${percent}%` : "—"}
      </span>
      {reset ? (
        <span
          className="inst-window-reset"
          title={resetTitle ?? reset}
          aria-label={resetTitle ?? reset}
        >
          <span aria-hidden="true" className="inst-window-reset-glyph">
            ◷
          </span>
          {reset}
        </span>
      ) : null}
    </span>
  );
}

/* ── Board ── */

function BoardTile({ channels }: { channels: BoardChannel[] }) {
  const totalPosts = channels.reduce((sum, c) => sum + c.entry_count, 0);
  const lastActivity = channels
    .map((c) => c.last_created_at)
    .filter(Boolean)
    .sort()
    .at(-1);
  // The API returns channels most-recently-active first.
  const topChannels = channels.slice(0, 3);

  if (channels.length === 0) {
    return (
      <Link className="inst" href="/board">
        <div className="inst-top">
          <span className="inst-name">Board</span>
          <span className="inst-go" aria-hidden="true">
            →
          </span>
        </div>
        <span className="inst-lamp tone-ok">Quiet</span>
        <h4 className="inst-headline">No channels</h4>
        <p className="inst-line">Sessions coordinate here</p>
      </Link>
    );
  }

  // A container (not a wrapping link) so each channel name can be its own link
  // back to that channel — the header link covers the whole-board case.
  return (
    <section className="inst">
      <Link className="inst-top inst-top-link" href="/board">
        <span className="inst-name">Board</span>
        <span className="inst-go" aria-hidden="true">
          →
        </span>
      </Link>
      <span className="inst-lamp tone-info">Blackboard</span>
      <h4 className="inst-headline">
        {channels.length} channel{channels.length === 1 ? "" : "s"}
      </h4>
      <p className="inst-line">
        <b>{totalPosts}</b> post{totalPosts === 1 ? "" : "s"}
        {lastActivity ? <> · {formatRelativeTime(lastActivity)}</> : null}
      </p>
      <ul className="inst-channels">
        {topChannels.map((channel) => (
          <li key={channel.channel}>
            <Link
              className="inst-channel"
              href={`/board?channel=${encodeURIComponent(channel.channel)}`}
            >
              <span className="inst-channel-name" title={channel.channel}>
                {channel.channel}
              </span>
              <span className="inst-channel-count">{channel.entry_count}</span>
            </Link>
          </li>
        ))}
      </ul>
    </section>
  );
}

/* ── Scheduled ── */

interface ScheduledItem {
  key: string;
  kind: "launch" | "message";
  when: number | null;
  whenLabel: string;
  target: string;
  preview: string;
  recurring: boolean;
}

function formatShortWhen(iso: string | null | undefined): string {
  if (!iso) return "queued";
  const target = Date.parse(iso);
  if (!Number.isFinite(target)) return "queued";
  const delta = target - Date.now();
  if (delta <= 0) return "due";
  const mins = Math.round(delta / 60000);
  if (mins < 60) return `in ${mins}m`;
  const hours = Math.floor(mins / 60);
  if (hours < 24) return `in ${hours}h`;
  return `in ${Math.floor(hours / 24)}d`;
}

function ScheduledTile({
  schedules,
  messageSchedules,
  sessions,
  onOpen,
}: {
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  sessions: SessionRecord[];
  onOpen: () => void;
}) {
  const pendingSessions = schedules.filter((s) => s.status === "pending");
  const pendingMessages = messageSchedules.filter((m) => m.status === "pending");
  const queued = pendingSessions.length + pendingMessages.length;

  if (queued === 0) {
    return (
      <button type="button" className="inst" onClick={onOpen}>
        <div className="inst-top">
          <span className="inst-name">Scheduled</span>
          <span className="inst-go" aria-hidden="true">
            →
          </span>
        </div>
        <span className="inst-lamp tone-ok">Nothing queued</span>
        <h4 className="inst-headline">No pending runs</h4>
        <p className="inst-line">Queue a launch or message</p>
      </button>
    );
  }

  const titleById = new Map(sessions.map((s) => [s.id, s.title]));
  const items: ScheduledItem[] = [
    ...pendingSessions.map((s) => ({
      key: `launch:${s.id}`,
      kind: "launch" as const,
      when: Date.parse(s.scheduled_at),
      whenLabel: formatShortWhen(s.scheduled_at),
      target: s.title?.trim() || `${s.backend} session`,
      preview: s.initial_prompt?.trim() || s.cwd,
      recurring: isRecurring(s),
    })),
    ...pendingMessages.map((m) => ({
      key: `message:${m.id}`,
      kind: "message" as const,
      when: m.scheduled_at ? Date.parse(m.scheduled_at) : null,
      whenLabel: formatShortWhen(m.scheduled_at),
      target: titleById.get(m.session_id)?.trim() || "session",
      preview: m.text?.trim() || "",
      recurring: isRecurring(m),
    })),
  ].sort((a, b) => (a.when ?? Infinity) - (b.when ?? Infinity));

  const shown = items.slice(0, 3);
  const overflow = queued - shown.length;

  return (
    <button type="button" className="inst" onClick={onOpen}>
      <div className="inst-top">
        <span className="inst-name">Scheduled</span>
        <span className="inst-go" aria-hidden="true">
          →
        </span>
      </div>
      <span className="inst-lamp tone-warn">{queued} queued</span>
      <ul className="inst-sched">
        {shown.map((item) => (
          <li className="inst-sched-item" key={item.key}>
            <span className="inst-sched-line">
              <span className={`inst-sched-kind kind-${item.kind}`}>
                {item.kind === "launch" ? "launch" : "msg"}
              </span>
              <span className="inst-sched-target" title={item.target}>
                {item.kind === "message" ? "→ " : ""}
                {item.target}
              </span>
              {item.recurring ? (
                <span className="inst-sched-recur" aria-label="recurring">
                  ↻
                </span>
              ) : null}
              <span className="inst-sched-when">{item.whenLabel}</span>
            </span>
            {item.preview ? (
              <span className="inst-sched-preview">{item.preview}</span>
            ) : null}
          </li>
        ))}
      </ul>
      {overflow > 0 ? <p className="inst-note">+{overflow} more</p> : null}
    </button>
  );
}
