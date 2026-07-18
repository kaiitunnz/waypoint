"use client";

import Link from "next/link";

import {
  formatRelativeTime,
  rateLimitUsageTone,
  rateLimitWindowPercent,
  usageTone,
  type UsageTone,
} from "@/lib/usage";
import type {
  BoardChannel,
  MessageSchedule,
  ScheduledSession,
  UsageDashboardBucket,
  UsageWindow,
} from "@/lib/types";

// Right-rail instrument tiles: compact, glanceable status headlines that link
// to (or open) the dedicated surfaces. They consume already-fetched data
// (Board, Scheduled) or one existing usage fetch (Telemetry) and never mount a
// full dashboard or add polling.

interface InstrumentRailProps {
  usageBuckets: UsageDashboardBucket[] | null;
  telemetryEnabled: boolean;
  boardChannels: BoardChannel[];
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  onOpenScheduled: () => void;
}

export function InstrumentRail({
  usageBuckets,
  telemetryEnabled,
  boardChannels,
  schedules,
  messageSchedules,
  onOpenScheduled,
}: InstrumentRailProps) {
  return (
    <aside className="rail" aria-label="Instruments">
      <TelemetryTile buckets={usageBuckets} telemetryEnabled={telemetryEnabled} />
      <ScheduledTile
        schedules={schedules}
        messageSchedules={messageSchedules}
        onOpen={onOpenScheduled}
      />
      <BoardTile channels={boardChannels} />
    </aside>
  );
}

/* ── Telemetry ── */

function findWindow(
  bucket: UsageDashboardBucket,
  kind: "5h" | "weekly",
): UsageWindow | null {
  for (const window of bucket.snapshot.windows) {
    const label = (window.label || "").toLowerCase();
    if (kind === "5h" && (label.includes("5h") || window.window_minutes === 300)) {
      return window;
    }
    if (
      kind === "weekly" &&
      (label.includes("week") || window.window_minutes === 7 * 24 * 60)
    ) {
      return window;
    }
  }
  return null;
}

function bucketPeak(bucket: UsageDashboardBucket): number {
  return Math.max(
    0,
    ...bucket.snapshot.windows.map((w) => rateLimitWindowPercent(w) ?? 0),
  );
}

function TelemetryTile({
  buckets,
  telemetryEnabled,
}: {
  buckets: UsageDashboardBucket[] | null;
  telemetryEnabled: boolean;
}) {
  // Degrade to a plain link when usage is unavailable or there are no accounts:
  // lamp keyed off the master telemetry opt-in rather than live figures.
  if (buckets === null || buckets.length === 0) {
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

  let tone: UsageTone = "good";
  for (const bucket of buckets) {
    const t = rateLimitUsageTone(bucket.snapshot);
    if (t === "danger") {
      tone = "danger";
      break;
    }
    if (t === "warn") tone = "warn";
  }
  const lampTone = tone === "good" ? "ok" : tone === "warn" ? "warn" : "danger";
  const lampText =
    tone === "good"
      ? "All clear"
      : tone === "warn"
        ? "Approaching limit"
        : "Quota critical";

  // Worst-first so the account nearest its limit reads first at a glance.
  const ordered = [...buckets].sort((a, b) => bucketPeak(b) - bucketPeak(a));

  return (
    <Link className="inst" href="/telemetry">
      <div className="inst-top">
        <span className="inst-name">Telemetry</span>
        <span className="inst-go" aria-hidden="true">
          →
        </span>
      </div>
      <span className={`inst-lamp tone-${lampTone}`}>{lampText}</span>
      <h4 className="inst-headline">
        {buckets.length} account{buckets.length === 1 ? "" : "s"}
      </h4>
      <ul className="inst-accounts">
        {ordered.map((bucket) => {
          const five = findWindow(bucket, "5h");
          const weekly = findWindow(bucket, "weekly");
          const fivePct = five ? rateLimitWindowPercent(five) : null;
          const weekPct = weekly ? rateLimitWindowPercent(weekly) : null;
          const peak = bucketPeak(bucket);
          return (
            <li className="inst-account" key={bucket.account_key}>
              <span className="inst-account-head">
                <span
                  className={`inst-account-dot tone-${toneOf(peak)}`}
                  aria-hidden="true"
                />
                <span className="inst-account-name" title={bucket.account_label}>
                  {bucket.account_label}
                </span>
              </span>
              <UsageWindowMeter label="5h" percent={fivePct} />
              <UsageWindowMeter label="wk" percent={weekPct} />
            </li>
          );
        })}
      </ul>
    </Link>
  );
}

function toneOf(percent: number | null): "ok" | "warn" | "danger" {
  const t = usageTone(percent);
  return t === "good" ? "ok" : t === "warn" ? "warn" : "danger";
}

// One labelled window row: a caption, a tone-filled meter, and the percentage —
// so both the 5h and the weekly window are shown graphically, not just numeric.
function UsageWindowMeter({
  label,
  percent,
}: {
  label: string;
  percent: number | null;
}) {
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
  // The API returns channels most-recently-active first; surface the top few
  // by name + post count — more informative than an abstract volume sparkline.
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

  return (
    <Link className="inst" href="/board">
      <div className="inst-top">
        <span className="inst-name">Board</span>
        <span className="inst-go" aria-hidden="true">
          →
        </span>
      </div>
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
          <li className="inst-channel" key={channel.channel}>
            <span className="inst-channel-name" title={channel.channel}>
              {channel.channel}
            </span>
            <span className="inst-channel-count">{channel.entry_count}</span>
          </li>
        ))}
      </ul>
    </Link>
  );
}

/* ── Scheduled ── */

function scheduleLabel(schedule: ScheduledSession): string {
  return (
    schedule.title?.trim() ||
    schedule.initial_prompt?.trim() ||
    `${schedule.backend} session`
  );
}

function formatCountdown(iso: string): string {
  const target = Date.parse(iso);
  if (!Number.isFinite(target)) return "queued";
  const deltaMs = target - Date.now();
  if (deltaMs <= 0) return "due now";
  const mins = Math.round(deltaMs / 60000);
  if (mins < 60) return `Next in ${mins}m`;
  const hours = Math.floor(mins / 60);
  const rem = mins % 60;
  if (hours < 24) return `Next in ${hours}h${rem ? ` ${rem}m` : ""}`;
  const days = Math.floor(hours / 24);
  return `Next in ${days}d ${hours % 24}h`;
}

function ScheduledTile({
  schedules,
  messageSchedules,
  onOpen,
}: {
  schedules: ScheduledSession[];
  messageSchedules: MessageSchedule[];
  onOpen: () => void;
}) {
  const pendingSessions = schedules.filter((s) => s.status === "pending");
  const pendingMessages = messageSchedules.filter((m) => m.status === "pending");
  const queued = pendingSessions.length + pendingMessages.length;

  const nextSession = [...pendingSessions].sort(
    (a, b) => Date.parse(a.scheduled_at) - Date.parse(b.scheduled_at),
  )[0];

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
      <span className="inst-lamp tone-warn">
        {queued} queued
      </span>
      <h4 className="inst-headline">
        {nextSession ? formatCountdown(nextSession.scheduled_at) : `${queued} queued`}
      </h4>
      <p className="inst-line">
        {nextSession
          ? scheduleLabel(nextSession)
          : `${pendingMessages.length} message${pendingMessages.length === 1 ? "" : "s"} queued`}
      </p>
      {nextSession ? (
        <p className="inst-note">
          {nextSession.backend} · {formatRelativeTime(nextSession.scheduled_at)}
        </p>
      ) : null}
    </button>
  );
}
