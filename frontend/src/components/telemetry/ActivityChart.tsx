"use client";

import { useId, useMemo, useState } from "react";

import { ChartTooltip, ChartTooltipState } from "@/components/telemetry/ChartTooltip";
import { DOW_LABELS, formatDayLabel, formatHourLabel } from "@/lib/telemetry";
import { TelemetryActivity } from "@/lib/types";

interface ActivityChartProps {
  activity: TelemetryActivity | null;
  loading: boolean;
}

const CHART_W = 640;
const CHART_H = 200;
const PAD_LEFT = 40;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 26;

const DAILY_SERIES: { key: keyof TelemetryActivity["daily"][number]; label: string; slot: number }[] = [
  { key: "user_turns", label: "User turns", slot: 1 },
  { key: "agent_turns", label: "Agent turns", slot: 2 },
  { key: "tool_calls", label: "Tool calls", slot: 3 },
  { key: "sessions_created", label: "Sessions created", slot: 4 },
];

function seriesColor(slot: number): string {
  return `var(--tm-series-${slot})`;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const residual = value / magnitude;
  const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  return niceResidual * magnitude;
}

export function ActivityChart({ activity, loading }: ActivityChartProps) {
  const [tooltip, setTooltip] = useState<ChartTooltipState | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const trendTitleId = useId();
  const heatmapTitleId = useId();

  const heatmapSummary = useMemo(() => {
    if (!activity || activity.heatmap.length === 0) return null;
    const peak = activity.heatmap.reduce((best, cell) => (cell.count > best.count ? cell : best));
    return `Busiest: ${DOW_LABELS[peak.dow] ?? peak.dow} at ${formatHourLabel(peak.hour)} (${peak.count} events)`;
  }, [activity]);

  if (loading && !activity) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (!activity) return null;

  const daily = activity.daily;
  const plotW = CHART_W - PAD_LEFT - PAD_RIGHT;
  const plotH = CHART_H - PAD_TOP - PAD_BOTTOM;
  const max = niceMax(
    Math.max(0, ...daily.flatMap((d) => DAILY_SERIES.map((s) => Number(d[s.key])))),
  );
  const stepX = daily.length > 1 ? plotW / (daily.length - 1) : 0;

  const heatmapByCell = new Map<string, number>();
  let heatmapMax = 0;
  for (const cell of activity.heatmap) {
    heatmapByCell.set(`${cell.dow}:${cell.hour}`, cell.count);
    if (cell.count > heatmapMax) heatmapMax = cell.count;
  }

  return (
    <>
      <section className="panel tm-chart-card" aria-labelledby={trendTitleId}>
        <header className="tm-chart-head">
          <h3 id={trendTitleId}>Session activity</h3>
        </header>
        {daily.length === 0 ? (
          <p className="muted tm-chart-empty">No activity in this range.</p>
        ) : (
          <svg
            viewBox={`0 0 ${CHART_W} ${CHART_H}`}
            className="tm-chart-svg"
            role="img"
            aria-label={`Daily user turns, agent turns, tool calls, and sessions created across ${daily.length} days, peaking at ${max}`}
          >
            <g transform={`translate(${PAD_LEFT},${PAD_TOP})`}>
              {[0, 0.5, 1].map((fraction) => {
                const y = plotH - plotH * fraction;
                return (
                  <g key={fraction}>
                    <line x1={0} y1={y} x2={plotW} y2={y} className="tm-chart-gridline" />
                    <text x={-8} y={y} className="tm-chart-axis-label" textAnchor="end" dy="0.32em">
                      {Math.round(max * fraction)}
                    </text>
                  </g>
                );
              })}

              {DAILY_SERIES.map((series) => {
                const points = daily
                  .map((day, i) => {
                    const value = Number(day[series.key]);
                    const x = i * stepX;
                    const y = plotH - (max > 0 ? (value / max) * plotH : 0);
                    return `${x},${y}`;
                  })
                  .join(" ");
                return (
                  <polyline
                    key={series.key}
                    points={points}
                    fill="none"
                    stroke={seriesColor(series.slot)}
                    strokeWidth={2}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                  />
                );
              })}

              {daily.map((day, i) => {
                const x = i * stepX;
                return (
                  <rect
                    key={day.day}
                    x={x - stepX / 2}
                    y={0}
                    width={Math.max(stepX, 1)}
                    height={plotH}
                    fill="transparent"
                    tabIndex={0}
                    role="img"
                    aria-label={`${formatDayLabel(day.day)}: ${day.user_turns} user turns, ${day.agent_turns} agent turns, ${day.tool_calls} tool calls, ${day.sessions_created} sessions created`}
                    className="tm-hover-column"
                    onMouseEnter={(event) =>
                      setTooltip({
                        left: event.clientX,
                        top: event.clientY,
                        title: formatDayLabel(day.day),
                        rows: DAILY_SERIES.map((series) => ({
                          key: series.key,
                          label: series.label,
                          value: String(day[series.key]),
                          color: seriesColor(series.slot),
                        })),
                      })
                    }
                    onMouseMove={(event) =>
                      setTooltip({
                        left: event.clientX,
                        top: event.clientY,
                        title: formatDayLabel(day.day),
                        rows: DAILY_SERIES.map((series) => ({
                          key: series.key,
                          label: series.label,
                          value: String(day[series.key]),
                          color: seriesColor(series.slot),
                        })),
                      })
                    }
                    onFocus={() =>
                      setTooltip({
                        left: 0,
                        top: 0,
                        title: formatDayLabel(day.day),
                        rows: DAILY_SERIES.map((series) => ({
                          key: series.key,
                          label: series.label,
                          value: String(day[series.key]),
                          color: seriesColor(series.slot),
                        })),
                      })
                    }
                    onMouseLeave={() => setTooltip(null)}
                    onBlur={() => setTooltip(null)}
                  />
                );
              })}
              <line x1={0} y1={plotH} x2={plotW} y2={plotH} className="tm-chart-axis" />
            </g>
          </svg>
        )}
        <ChartTooltip state={tooltip} />
        {daily.length > 0 ? (
          <div className="tm-chart-legend" role="list" aria-label="Series">
            {DAILY_SERIES.map((series) => (
              <span key={series.key} className="tm-legend-item" role="listitem">
                <span
                  className="tm-legend-swatch"
                  aria-hidden="true"
                  style={{ background: seriesColor(series.slot) }}
                />
                {series.label}
              </span>
            ))}
          </div>
        ) : null}

        <button
          type="button"
          className="tm-table-toggle"
          onClick={() => setTableOpen((v) => !v)}
          aria-expanded={tableOpen}
        >
          {tableOpen ? "Hide data table" : "View as table"}
        </button>
        {tableOpen ? (
          <div className="tm-table-wrap">
            <table className="tm-data-table">
              <caption className="sr-only">Daily session activity</caption>
              <thead>
                <tr>
                  <th scope="col">Day</th>
                  {DAILY_SERIES.map((series) => (
                    <th scope="col" key={series.key}>
                      {series.label}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {daily.map((day) => (
                  <tr key={day.day}>
                    <th scope="row">{day.day}</th>
                    {DAILY_SERIES.map((series) => (
                      <td key={series.key}>{day[series.key]}</td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
      </section>

      <section className="panel tm-chart-card" aria-labelledby={heatmapTitleId}>
        <header className="tm-chart-head">
          <h3 id={heatmapTitleId}>Activity heatmap</h3>
        </header>
        {activity.heatmap.length === 0 ? (
          <p className="muted tm-chart-empty">No activity in this range.</p>
        ) : (
          <>
            <p className="tm-chart-summary">{heatmapSummary}</p>
            <div className="tm-heatmap-scroll">
              <table className="tm-heatmap" role="table" aria-label="Activity by day of week and hour">
                <thead>
                  <tr>
                    <th scope="col" />
                    {Array.from({ length: 24 }, (_, hour) => (
                      <th key={hour} scope="col" className="tm-heatmap-hour">
                        {hour % 3 === 0 ? formatHourLabel(hour) : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {DOW_LABELS.map((label, dow) => (
                    <tr key={label}>
                      <th scope="row" className="tm-heatmap-dow">
                        {label}
                      </th>
                      {Array.from({ length: 24 }, (_, hour) => {
                        const count = heatmapByCell.get(`${dow}:${hour}`) ?? 0;
                        const intensity = heatmapMax > 0 ? count / heatmapMax : 0;
                        return (
                          <td key={hour} className="tm-heatmap-cell-wrap">
                            <button
                              type="button"
                              className="tm-heatmap-cell"
                              style={{
                                background:
                                  count === 0
                                    ? "transparent"
                                    : `color-mix(in srgb, var(--tm-series-1) ${Math.round(18 + intensity * 82)}%, var(--bg-card))`,
                              }}
                              aria-label={`${label} ${formatHourLabel(hour)}: ${count} event${count === 1 ? "" : "s"}`}
                              onMouseEnter={(event) =>
                                setTooltip({
                                  left: event.clientX,
                                  top: event.clientY,
                                  title: `${label} · ${formatHourLabel(hour)}`,
                                  rows: [{ key: "count", label: "events", value: String(count) }],
                                })
                              }
                              onFocus={() =>
                                setTooltip({
                                  left: 0,
                                  top: 0,
                                  title: `${label} · ${formatHourLabel(hour)}`,
                                  rows: [{ key: "count", label: "events", value: String(count) }],
                                })
                              }
                              onMouseLeave={() => setTooltip(null)}
                              onBlur={() => setTooltip(null)}
                            />
                          </td>
                        );
                      })}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <ChartTooltip state={tooltip} />
          </>
        )}
      </section>
    </>
  );
}
