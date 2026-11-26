import Link from "next/link";

import type { BoardSummary } from "@waypoint/web-core/hooks/useBoardSummary";
import type { UsageSummaryState } from "@waypoint/web-core/hooks/useUsageSummary";
import { formatRelativeTime, type UsageTone } from "@waypoint/web-core/usage";
import {
  accountWindows,
  mostConstrainedWindow,
  orderBucketsWorstFirst,
  type ScheduledSummary,
} from "@waypoint/web-core/usageSummary";

import { Icon } from "@/components/ui/Icon";
import { IconButton } from "@/components/ui/IconButton";
import { Meter, type MeterTone } from "@/components/ui/Meter";
import { SectionHeading } from "@/components/ui/SectionHeading";

import styles from "./Summaries.module.css";

const METER_TONE: Record<UsageTone, MeterTone> = { good: "calm", warn: "warn", danger: "danger" };

const pressure = (tone: UsageTone) => (tone === "good" ? "" : styles.hot);

export function LimitsStrip({ usage, now }: { usage: UsageSummaryState; now: number }) {
  const top = usage.buckets ? mostConstrainedWindow(usage.buckets, now) : null;
  if (!top) return null;
  return (
    <Link href="/telemetry" className={styles.strip}>
      <span className={styles.who}>{top.accountLabel}</span>
      <span className={styles.stripWindow}>{top.label}</span>
      <Meter value={top.percent} tone={METER_TONE[top.tone]} label={`${top.accountLabel} ${top.label} used`} className={styles.stripMeter} />
      <span className={pressure(top.tone)}>{top.percent}%</span>
      {top.resetsIn ? <span className={styles.faint}>resets in {top.resetsIn}</span> : null}
      <span className={styles.chevron}>
        <Icon name="chevron-right" size={16} strokeWidth={2} />
      </span>
    </Link>
  );
}

export function LimitsReadout({ usage, now }: { usage: UsageSummaryState; now: number }) {
  const buckets = usage.buckets ? orderBucketsWorstFirst(usage.buckets) : [];
  return (
    <section>
      <SectionHeading
        title="Limits"
        action={
          <span className={styles.actions}>
            <IconButton
              label={usage.refreshing ? "Refreshing usage" : "Refresh usage"}
              size="sm"
              disabled={usage.refreshing}
              onClick={usage.refresh}
            >
              <Icon name="refresh" size={16} />
            </IconButton>
            <Link href="/telemetry">Usage</Link>
          </span>
        }
      />
      {buckets.length === 0 ? (
        <p className={styles.empty}>{usage.buckets === null ? "Usage unavailable" : "No accounts"}</p>
      ) : (
        <div className={styles.accounts}>
          {buckets.map((bucket) => {
            const windows = accountWindows(bucket, now);
            return (
              <div key={bucket.account_key}>
                {windows.map((reading, index) => (
                  <div key={reading.kind} className={styles.window}>
                    <span className={index === 0 ? styles.accountName : styles.reset}>
                      {index === 0
                        ? bucket.account_label
                        : reading.resetsIn
                          ? `resets in ${reading.resetsIn}`
                          : ""}
                    </span>
                    <span className={styles.windowLabel}>{reading.label}</span>
                    <Meter
                      value={reading.percent}
                      tone={METER_TONE[reading.tone]}
                      label={`${bucket.account_label} ${reading.label} used`}
                      className={styles.windowMeter}
                    />
                    <span className={`${styles.percent} ${pressure(reading.tone)}`}>{reading.percent}%</span>
                  </div>
                ))}
              </div>
            );
          })}
        </div>
      )}
    </section>
  );
}

export function ScheduledSummaryList({ summary, onOpen }: { summary: ScheduledSummary; onOpen: () => void }) {
  if (summary.total === 0) return null;
  return (
    <section>
      <SectionHeading
        title="Scheduled"
        count={summary.queued}
        action={
          <button type="button" onClick={onOpen}>
            All
          </button>
        }
      />
      {summary.queued === 0 ? <p className={styles.empty}>Nothing pending</p> : null}
      <ul className={styles.scheduled}>
        {summary.items.map((item) => (
          <li key={item.key} className={styles.item}>
            <span className={styles.glyph} aria-hidden="true">
              {item.recurring ? "↻" : "◷"}
            </span>
            <span className={styles.itemText}>
              <span className={styles.itemTitle}>
                {item.kind === "launch" ? `Launch ${item.target}` : item.preview || "Message"}
              </span>
              <span className={styles.itemSub}>
                {item.kind === "launch"
                  ? item.recurring
                    ? "Repeats"
                    : item.preview
                  : `Message to ${item.target}`}
              </span>
            </span>
            <span className={styles.when}>{item.whenLabel}</span>
          </li>
        ))}
      </ul>
      {summary.overflow > 0 ? <p className={styles.overflow}>+{summary.overflow} more</p> : null}
    </section>
  );
}

export function BoardSummaryCard({ summary, hideEmpty }: { summary: BoardSummary | null; hideEmpty?: boolean }) {
  // Nothing to say on a small screen where the Board tab is one tap away.
  if (hideEmpty && (summary === null || (summary.kind === "channels" && summary.summary.channels === 0))) {
    return null;
  }
  return (
    <section>
      <SectionHeading title="Board" action={<Link href="/board">Open board</Link>} />
      {summary === null ? null : summary.kind === "tickets" ? (
        <div className={styles.counts}>
          <Count value={summary.counts.needYou} label="need you" attention />
          <Count value={summary.counts.inFlight} label="in flight" />
          <Count value={summary.counts.blocked} label="blocked" />
          <Count value={summary.counts.merged} label="merged" />
        </div>
      ) : summary.summary.channels === 0 ? (
        <p className={styles.empty}>No channels</p>
      ) : (
        <p className={styles.channels}>
          {summary.summary.channels} {summary.summary.channels === 1 ? "channel" : "channels"},{" "}
          {summary.summary.posts} {summary.summary.posts === 1 ? "post" : "posts"}
          {summary.summary.lastActivity ? (
            <span className={styles.faint}> · {formatRelativeTime(summary.summary.lastActivity)}</span>
          ) : null}
        </p>
      )}
    </section>
  );
}

function Count({ value, label, attention }: { value: number; label: string; attention?: boolean }) {
  return (
    <span className={styles.count}>
      <b className={attention && value > 0 ? styles.need : undefined}>{value}</b>
      {label}
    </span>
  );
}
