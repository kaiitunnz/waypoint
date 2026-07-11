"use client";

import { useId, useState } from "react";

import { UsageBar } from "@/components/UsageReadout";
import { UsageDial } from "@/components/UsageDial";
import { ChartTooltip, ChartTooltipState } from "@/components/telemetry/ChartTooltip";
import { shortId } from "@/lib/telemetry";
import { TelemetryHealth } from "@/lib/types";
import { formatRelativeTime, usageTone, UsageTone } from "@/lib/usage";

interface HealthPanelProps {
  health: TelemetryHealth | null;
  loading: boolean;
}

const SPARK_W = 320;
const SPARK_H = 56;
// A sparkline's job is local trend shape, not absolute magnitude (the exact
// number already reads via the paired dial/numeral) — so it autoscales to
// its own observed range rather than a fixed 0-100 domain. A mostly-null
// low-usage series (a 5h/weekly limit window sitting at a few percent) would
// otherwise flatline against the bottom edge with the rest of the box empty.
// A floor keeps genuinely flat/near-flat data from collapsing to a hairline.
const SPARK_MIN_SPAN = 20;

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

function Sparkline({
  points,
  onHover,
}: {
  points: { label: string; percent: number | null }[];
  onHover: (state: ChartTooltipState | null) => void;
}) {
  if (points.length === 0) {
    return <p className="muted tm-sparkline-empty">No samples in range.</p>;
  }
  const [domainMin, domainMax] = sparklineDomain(
    points.filter((p): p is { label: string; percent: number } => p.percent !== null).map((p) => p.percent),
  );
  const domainSpan = domainMax - domainMin || 1;
  const toY = (percent: number) =>
    SPARK_H - ((Math.min(100, Math.max(0, percent)) - domainMin) / domainSpan) * SPARK_H;
  const stepX = points.length > 1 ? SPARK_W / (points.length - 1) : 0;
  const segments: string[] = [];
  // A sample with no adjacent non-null neighbor never joins a 2+ point
  // polyline segment, so it needs its own visible mark — otherwise a sparse,
  // mostly-null series (a lightly-used 5h/weekly limit window) renders as
  // nothing at all rather than as real, if sparse, data.
  const isolated = new Set<number>();
  let current: { i: number; xy: string }[] = [];
  const flush = () => {
    if (current.length > 1) {
      segments.push(current.map((p) => p.xy).join(" "));
    } else if (current.length === 1) {
      isolated.add(current[0].i);
    }
    current = [];
  };
  points.forEach((point, i) => {
    if (point.percent === null) {
      flush();
      return;
    }
    const x = i * stepX;
    current.push({ i, xy: `${x},${toY(point.percent)}` });
  });
  flush();

  return (
    <div className="tm-sparkline-wrap">
      <span className="tm-sparkline-scale muted">
        {Math.round(domainMin)}–{Math.round(domainMax)}% shown
      </span>
      <svg
        viewBox={`0 0 ${SPARK_W} ${SPARK_H}`}
        className="tm-sparkline"
        role="img"
        aria-label={`Occupancy over time, from ${points[0].label} to ${points[points.length - 1].label}, ranging ${Math.round(domainMin)}% to ${Math.round(domainMax)}%`}
      >
        <line x1={0} y1={1} x2={SPARK_W} y2={1} className="tm-chart-gridline" />
        <line x1={0} y1={SPARK_H - 1} x2={SPARK_W} y2={SPARK_H - 1} className="tm-chart-gridline" />
        {segments.map((segment, i) => (
          <polyline
            key={i}
            points={segment}
            fill="none"
            stroke="var(--tm-series-1)"
            strokeWidth={2}
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        ))}
        {points.map((point, i) => {
          if (point.percent === null) return null;
          const x = i * stepX;
          const y = toY(point.percent);
          return (
            <g key={i}>
              {isolated.has(i) ? (
                <circle cx={x} cy={y} r={3} fill="var(--tm-series-1)" stroke="var(--bg-card)" strokeWidth={2} />
              ) : null}
              <circle
                cx={x}
                cy={y}
                r={7}
                fill="transparent"
                tabIndex={0}
                role="img"
                aria-label={`${point.label}: ${Math.round(point.percent)}%`}
                className="tm-hover-point"
                onMouseEnter={(event) =>
                  onHover({
                    left: event.clientX,
                    top: event.clientY,
                    title: point.label,
                    rows: [{ key: "pct", label: "occupancy", value: `${Math.round(point.percent!)}%` }],
                  })
                }
                onFocus={() =>
                  onHover({
                    left: 0,
                    top: 0,
                    title: point.label,
                    rows: [{ key: "pct", label: "occupancy", value: `${Math.round(point.percent!)}%` }],
                  })
                }
                onMouseLeave={() => onHover(null)}
                onBlur={() => onHover(null)}
              />
            </g>
          );
        })}
      </svg>
    </div>
  );
}

export function HealthPanel({ health, loading }: HealthPanelProps) {
  const [tooltip, setTooltip] = useState<ChartTooltipState | null>(null);
  const contextTitleId = useId();
  const limitsTitleId = useId();

  if (loading && !health) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (!health) return null;

  const contextSeriesPoints = health.context.series.map((point) => ({
    label: new Date(point.bucket_start).toLocaleString([], {
      month: "short",
      day: "numeric",
      hour: "numeric",
    }),
    percent: point.peak_percent,
  }));

  return (
    <>
      <section className="panel tm-chart-card" aria-labelledby={contextTitleId}>
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
                    {shortId(snapshot.session_id)}
                    {snapshot.stale ? <span className="tm-health-stale"> · stale</span> : null}
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
        <Sparkline points={contextSeriesPoints} onHover={setTooltip} />
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
        ) : health.limits.current.length === 0 ? (
          <p className="muted tm-chart-empty">No live limit snapshots.</p>
        ) : (
          <>
            <div className="tm-dial-grid">
              {health.limits.current.map((snapshot) => (
                <div
                  key={`${snapshot.backend}:${snapshot.account_key}:${snapshot.window_id}`}
                  className="tm-dial-cell"
                >
                  <UsageDial
                    percent={snapshot.used_percent}
                    tone={usageTone(snapshot.used_percent)}
                    label={snapshot.label ?? snapshot.window_id}
                    caption={`${snapshot.backend}${snapshot.stale ? " · stale" : ""}`}
                    size="md"
                  />
                </div>
              ))}
            </div>
            {health.limits.series.map((series) => (
              <div key={`${series.backend}:${series.account_key}:${series.window_id}`}>
                <p className="tm-chart-subhead">
                  {series.label ?? series.window_id} · {series.backend}
                </p>
                <Sparkline
                  points={series.points.map((point) => ({
                    label: new Date(point.bucket_start).toLocaleString([], {
                      month: "short",
                      day: "numeric",
                      hour: "numeric",
                    }),
                    percent: point.used_percent,
                  }))}
                  onHover={setTooltip}
                />
              </div>
            ))}
          </>
        )}
        <ChartTooltip state={tooltip} />
      </section>
    </>
  );
}
