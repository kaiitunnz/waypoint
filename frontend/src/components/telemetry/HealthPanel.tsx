"use client";

import { useId, useMemo, useState } from "react";

import { UsageBar } from "@/components/UsageReadout";
import { ChartTooltip, ChartTooltipState } from "@/components/telemetry/ChartTooltip";
import { shortId } from "@/lib/telemetry";
import { LimitSeries, LimitSnapshotView, TelemetryHealth } from "@/lib/types";
import { formatRelativeTime, usageTone, UsageTone } from "@/lib/usage";

interface HealthPanelProps {
  health: TelemetryHealth | null;
  loading: boolean;
}

const SPARK_W = 200;
const SPARK_H = 30;
// A mini-trend shows local shape, not magnitude (the paired bar/numeral already
// carries the exact value), so it autoscales to its own observed range. A floor
// keeps a near-flat low-usage window from collapsing to a hairline.
const SPARK_MIN_SPAN = 15;

function toneGlyph(tone: UsageTone): string {
  if (tone === "danger") return "▲";
  if (tone === "warn") return "■";
  return "●";
}

function toneWord(tone: UsageTone): string {
  if (tone === "danger") return "critical";
  if (tone === "warn") return "warning";
  return "nominal";
}

function sparklineDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 100];
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min;
  if (span < SPARK_MIN_SPAN) {
    const mid = (min + max) / 2;
    const half = SPARK_MIN_SPAN / 2;
    const lo = Math.max(0, mid - half);
    return [lo, Math.min(100, lo + SPARK_MIN_SPAN)];
  }
  const pad = span * 0.1;
  return [Math.max(0, min - pad), Math.min(100, max + pad)];
}

// One compact trend line through every non-null sample in time order. Bridging
// short null gaps keeps the line connected and readable, rather than scattering
// a lightly-sampled window into disconnected dots with dead vertical space.
function MiniTrend({
  points,
  onHover,
}: {
  points: { label: string; percent: number | null }[];
  onHover: (state: ChartTooltipState | null) => void;
}) {
  const real = points
    .map((point, index) => ({ ...point, index }))
    .filter((point): point is { label: string; percent: number; index: number } => point.percent !== null);

  if (real.length === 0) {
    return <p className="muted tm-sparkline-empty">No recent samples in range.</p>;
  }

  const latest = real[real.length - 1];
  if (real.length === 1) {
    return (
      <p className="tm-sparkline-single muted">
        <span aria-hidden="true">◦</span> Single sample · {latest.label} at{" "}
        {Math.round(latest.percent)}%
      </p>
    );
  }

  const [domainMin, domainMax] = sparklineDomain(real.map((p) => p.percent));
  const domainSpan = domainMax - domainMin || 1;
  const stepX = points.length > 1 ? SPARK_W / (points.length - 1) : 0;
  const toY = (percent: number) =>
    SPARK_H - 1 - ((Math.min(100, Math.max(0, percent)) - domainMin) / domainSpan) * (SPARK_H - 2);
  const coords = real.map((point) => ({ ...point, x: point.index * stepX, y: toY(point.percent) }));
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const area = `M ${coords[0].x},${SPARK_H} ${coords.map((c) => `L ${c.x},${c.y}`).join(" ")} L ${
    coords[coords.length - 1].x
  },${SPARK_H} Z`;

  return (
    <div className="tm-sparkline-wrap">
      <span className="tm-sparkline-scale muted">
        {Math.round(domainMin)}–{Math.round(domainMax)}%
      </span>
      <svg
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        className="tm-sparkline"
        preserveAspectRatio="none"
        role="img"
        aria-label={`Trend from ${real[0].label} to ${latest.label}, ranging ${Math.round(
          domainMin,
        )}% to ${Math.round(domainMax)}%, latest ${Math.round(latest.percent)}%`}
      >
        <path d={area} className="tm-sparkline-area" />
        <polyline
          points={line}
          fill="none"
          stroke="var(--tm-series-1)"
          strokeWidth={1.75}
          strokeLinecap="round"
          strokeLinejoin="round"
          vectorEffect="non-scaling-stroke"
        />
        {coords.map((c) => (
          <circle
            key={c.index}
            cx={c.x}
            cy={c.y}
            r={5}
            fill="transparent"
            tabIndex={0}
            role="img"
            aria-label={`${c.label}: ${Math.round(c.percent)}%`}
            className="tm-hover-point"
            onMouseEnter={(event) =>
              onHover({
                left: event.clientX,
                top: event.clientY,
                title: c.label,
                rows: [{ key: "pct", label: "occupancy", value: `${Math.round(c.percent)}%` }],
              })
            }
            onFocus={() =>
              onHover({
                left: 0,
                top: 0,
                title: c.label,
                rows: [{ key: "pct", label: "occupancy", value: `${Math.round(c.percent)}%` }],
              })
            }
            onMouseLeave={() => onHover(null)}
            onBlur={() => onHover(null)}
          />
        ))}
      </svg>
    </div>
  );
}

function seriesLabel(point: { bucket_start: string }): string {
  return new Date(point.bucket_start).toLocaleString([], {
    month: "short",
    day: "numeric",
    hour: "numeric",
  });
}

interface AccountGroup {
  backend: string;
  accountKey: string;
  accountLabel: string | null;
  profileLabel: string | null;
  windows: { snapshot: LimitSnapshotView; series: LimitSeries | undefined }[];
}

// The humanized, always-safe display name ("Default" / a profile name like
// "nus"). Asserted as optional so this compiles while the field is landing on
// LimitSnapshotView; falls back to the pseudonymous account key until present.
function limitProfileLabel(view: LimitSnapshotView): string | null {
  const label = (view as LimitSnapshotView & { profile_label?: string | null }).profile_label;
  return label && label.trim().length > 0 ? label : null;
}

function groupLimitsByAccount(limits: TelemetryHealth["limits"]): AccountGroup[] {
  const seriesByWindow = new Map<string, LimitSeries>();
  for (const series of limits.series) {
    seriesByWindow.set(`${series.backend}:${series.account_key}:${series.window_id}`, series);
  }
  const groups = new Map<string, AccountGroup>();
  for (const snapshot of limits.current) {
    const groupKey = `${snapshot.backend}:${snapshot.account_key}`;
    let group = groups.get(groupKey);
    if (!group) {
      group = {
        backend: snapshot.backend,
        accountKey: snapshot.account_key,
        accountLabel: snapshot.account_label,
        profileLabel: limitProfileLabel(snapshot),
        windows: [],
      };
      groups.set(groupKey, group);
    } else if (group.profileLabel === null) {
      group.profileLabel = limitProfileLabel(snapshot);
    }
    group.windows.push({
      snapshot,
      series: seriesByWindow.get(`${snapshot.backend}:${snapshot.account_key}:${snapshot.window_id}`),
    });
  }
  return [...groups.values()];
}

export function HealthPanel({ health, loading }: HealthPanelProps) {
  const [tooltip, setTooltip] = useState<ChartTooltipState | null>(null);
  const contextTitleId = useId();
  const limitsTitleId = useId();

  const accountGroups = useMemo(
    () => (health ? groupLimitsByAccount(health.limits) : []),
    [health],
  );

  if (loading && !health) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (!health) return null;

  const contextSeriesPoints = health.context.series.map((point) => ({
    label: seriesLabel(point),
    percent: point.peak_percent,
  }));

  return (
    <>
      <section id="tm-health-anchor" className="panel tm-chart-card" aria-labelledby={contextTitleId}>
        <header className="tm-chart-head">
          <h3 id={contextTitleId}>Context health</h3>
        </header>
        {health.context.current.length === 0 ? (
          <p className="muted tm-chart-empty">No live context snapshots.</p>
        ) : (
          <ul className="tm-health-list">
            {health.context.current.map((snapshot) => {
              const tone = usageTone(snapshot.percent);
              return (
                <li key={snapshot.session_id} className={`tm-health-row tone-${tone}`}>
                  <span className="tm-health-glyph" aria-hidden="true">
                    {toneGlyph(tone)}
                  </span>
                  <span className="tm-health-label">
                    <span className="tm-health-id" title={snapshot.session_id}>
                      {shortId(snapshot.session_id)}
                    </span>
                    {snapshot.stale ? <span className="tm-health-stale">stale</span> : null}
                  </span>
                  <UsageBar percent={snapshot.percent} tone={tone} disabled={snapshot.percent === null} />
                  <span className="tm-health-value">
                    {snapshot.percent !== null ? `${Math.round(snapshot.percent)}%` : "—"}
                    <span className="tm-health-tone-word"> {toneWord(tone)}</span>
                  </span>
                  <span className="tm-health-time">{formatRelativeTime(snapshot.updated_at)}</span>
                </li>
              );
            })}
          </ul>
        )}
        <p className="tm-chart-subhead">Peak occupancy over time</p>
        <MiniTrend points={contextSeriesPoints} onHover={setTooltip} />
        <ChartTooltip state={tooltip} />
      </section>

      <section className="panel tm-chart-card" aria-labelledby={limitsTitleId}>
        <header className="tm-chart-head">
          <h3 id={limitsTitleId}>Provider limits</h3>
        </header>
        {health.limits.hidden ? (
          <p className="muted tm-chart-empty">
            {health.limits.hidden_reason ?? "Hidden while a session filter is active."}
          </p>
        ) : accountGroups.length === 0 ? (
          <p className="muted tm-chart-empty">No live limit snapshots.</p>
        ) : (
          <div className="tm-account-groups">
            {accountGroups.map((group) => (
              <article key={`${group.backend}:${group.accountKey}`} className="tm-account-group">
                <header className="tm-account-head">
                  <span className="tm-account-name">
                    {group.profileLabel ?? group.accountLabel ?? group.accountKey}
                  </span>
                  <span className="tm-account-backend">{group.backend}</span>
                </header>
                <ul className="tm-window-list">
                  {group.windows.map(({ snapshot, series }) => {
                    const tone = usageTone(snapshot.used_percent);
                    const points = (series?.points ?? []).map((point) => ({
                      label: seriesLabel(point),
                      percent: point.used_percent,
                    }));
                    return (
                      <li
                        key={snapshot.window_id}
                        className={`tm-window-row tone-${tone}`}
                      >
                        <div className="tm-window-head">
                          <span className="tm-window-label">
                            <span className="tm-window-glyph" aria-hidden="true">
                              {toneGlyph(tone)}
                            </span>
                            {snapshot.label ?? snapshot.window_id}
                            {snapshot.stale ? <span className="tm-health-stale">stale</span> : null}
                          </span>
                          <span className="tm-window-value">
                            {Math.round(snapshot.used_percent)}%
                            <span className="tm-health-tone-word"> {toneWord(tone)}</span>
                          </span>
                        </div>
                        <UsageBar percent={snapshot.used_percent} tone={tone} />
                        <div className="tm-window-trend">
                          <MiniTrend points={points} onHover={setTooltip} />
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </article>
            ))}
          </div>
        )}
        <ChartTooltip state={tooltip} />
      </section>
    </>
  );
}
