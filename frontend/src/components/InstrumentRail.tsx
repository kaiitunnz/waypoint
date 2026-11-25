"use client";

import Link from "next/link";

import {
  formatRateLimitWindowReset,
  formatRateLimitWindowResetShort,
  formatRelativeTime,
  rateLimitWindowPercent,
  usageTone,
} from "@waypoint/web-core/usage";
import {
  bucketPeak,
  bucketSourceLabel,
  findUsageWindow,
  orderBucketsWorstFirst,
  overallUsageTone,
  summarizeChannels,
  summarizeScheduled,
  unresolvedProviderHealth,
} from "@waypoint/web-core/usageSummary";
import type { BackendCatalog } from "@waypoint/web-core/backends";
import type {
  BoardChannel,
  MessageSchedule,
  ProviderUsageStatus,
  ScheduledSession,
  SessionRecord,
  UsageDashboardBucket,
  UsageWindow,
} from "@waypoint/web-core/types";

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
  const providerHealth = unresolvedProviderHealth(buckets ?? [], providers);

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
  const tone = overallUsageTone(accounts, providerHealth);
  const lampTone = tone === "good" ? "ok" : tone === "warn" ? "warn" : "danger";
  const lampText =
    tone === "good"
      ? "All clear"
      : tone === "warn"
        ? "Approaching limit"
        : "Quota critical";

  const ordered = orderBucketsWorstFirst(accounts);
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
                <span className="inst-account-src" title={bucketSourceLabel(bucket, catalog)}>
                  {bucketSourceLabel(bucket, catalog)}
                </span>
              </span>
              <UsageWindowMeter label="5h" window={findUsageWindow(bucket, "5h")} />
              <UsageWindowMeter label="wk" window={findUsageWindow(bucket, "weekly")} />
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
  const { posts: totalPosts, lastActivity, top: topChannels } = summarizeChannels(channels);

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
  const titleById = new Map(sessions.map((session) => [session.id, session.title]));
  const { queued, items: shown, overflow } = summarizeScheduled(
    schedules,
    messageSchedules,
    titleById,
  );

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
