"use client";

import { useId, useMemo, useState } from "react";

import { ChartTooltip, ChartTooltipState } from "@/components/telemetry/ChartTooltip";
import { DOW_LABELS, formatDayLabel, formatHourLabel } from "@/lib/telemetry";
import { ActivityDaily, TelemetryActivity } from "@/lib/types";

interface ActivityChartProps {
  activity: TelemetryActivity | null;
  loading: boolean;
}

const MINI_W = 240;
const MINI_H = 60;
const MINI_PAD = 4;

const DAILY_SERIES: { key: keyof ActivityDaily; label: string; slot: number }[] = [
  { key: "tool_calls", label: "Tool calls", slot: 3 },
  { key: "agent_turns", label: "Agent turns", slot: 2 },
  { key: "user_turns", label: "User turns", slot: 1 },
  { key: "sessions_created", label: "Sessions created", slot: 5 },
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

// Python weekday (Mon=0…Sun=6) from a host-tz calendar day string, matching
// the backend heatmap bucketing so the derived date labels line up with the rows.
function pythonDow(day: string): number {
  const parsed = new Date(`${day}T00:00:00`);
  if (Number.isNaN(parsed.getTime())) return -1;
  return (parsed.getDay() + 6) % 7;
}

// Each heatmap row is an aggregate over every day in the range that fell on
// that weekday — not a single date. Surfacing the actual dates each row covers
// is what makes "where did Sunday come from" self-evidently correct.
function formatDowDates(days: string[]): string {
  if (days.length === 0) return "no days";
  if (days.length === 1) return formatDayLabel(days[0]);
  const sorted = [...days].sort();
  return `${formatDayLabel(sorted[0])} – ${formatDayLabel(sorted[sorted.length - 1])} · ${days.length}×`;
}

function MiniTrend({
  daily,
  seriesKey,
  label,
  color,
  onHover,
}: {
  daily: ActivityDaily[];
  seriesKey: keyof ActivityDaily;
  label: string;
  color: string;
  onHover: (state: ChartTooltipState | null) => void;
}) {
  const values = daily.map((day) => Number(day[seriesKey]));
  const total = values.reduce((sum, v) => sum + v, 0);
  const peak = Math.max(0, ...values);
  const max = niceMax(peak);
  const plotW = MINI_W - MINI_PAD * 2;
  const plotH = MINI_H - MINI_PAD * 2;
  const stepX = daily.length > 1 ? plotW / (daily.length - 1) : 0;

  const coords = daily.map((_, i) => {
    const x = MINI_PAD + i * stepX;
    const y = MINI_PAD + plotH - (max > 0 ? (values[i] / max) * plotH : 0);
    return { x, y };
  });
  const line = coords.map((c) => `${c.x},${c.y}`).join(" ");
  const area =
    coords.length > 0
      ? `M ${coords[0].x},${MINI_PAD + plotH} ${coords.map((c) => `L ${c.x},${c.y}`).join(" ")} L ${
          coords[coords.length - 1].x
        },${MINI_PAD + plotH} Z`
      : "";

  return (
    <div className="tm-smallmult">
      <div className="tm-smallmult-head">
        <span className="tm-smallmult-name">
          <span className="tm-stat-swatch" aria-hidden="true" style={{ background: color }} />
          {label}
        </span>
        <span className="tm-smallmult-total">{total.toLocaleString()}</span>
      </div>
      <svg
        viewBox={`0 0 ${MINI_W} ${MINI_H}`}
        className="tm-smallmult-svg"
        preserveAspectRatio="none"
        role="img"
        aria-label={`${label}: ${total.toLocaleString()} total across ${daily.length} day${
          daily.length === 1 ? "" : "s"
        }, peaking at ${peak.toLocaleString()} per day`}
      >
        <line
          x1={MINI_PAD}
          y1={MINI_H - MINI_PAD}
          x2={MINI_W - MINI_PAD}
          y2={MINI_H - MINI_PAD}
          className="tm-chart-gridline"
        />
        {area ? <path d={area} fill={color} className="tm-smallmult-area" /> : null}
        {coords.length > 1 ? (
          <polyline
            points={line}
            fill="none"
            stroke={color}
            strokeWidth={1.75}
            strokeLinecap="round"
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        ) : coords.length === 1 ? (
          <circle cx={coords[0].x} cy={coords[0].y} r={2.5} fill={color} />
        ) : null}
        {daily.map((day, i) => (
          <rect
            key={day.day}
            x={coords[i].x - (stepX || plotW) / 2}
            y={0}
            width={Math.max(stepX || plotW, 1)}
            height={MINI_H}
            fill="transparent"
            tabIndex={0}
            role="img"
            aria-label={`${formatDayLabel(day.day)}: ${values[i].toLocaleString()} ${label.toLowerCase()}`}
            className="tm-hover-column"
            onMouseEnter={(event) =>
              onHover({
                left: event.clientX,
                top: event.clientY,
                title: formatDayLabel(day.day),
                rows: [{ key: label, label, value: values[i].toLocaleString(), color }],
              })
            }
            onMouseMove={(event) =>
              onHover({
                left: event.clientX,
                top: event.clientY,
                title: formatDayLabel(day.day),
                rows: [{ key: label, label, value: values[i].toLocaleString(), color }],
              })
            }
            onFocus={() =>
              onHover({
                left: 0,
                top: 0,
                title: formatDayLabel(day.day),
                rows: [{ key: label, label, value: values[i].toLocaleString(), color }],
              })
            }
            onMouseLeave={() => onHover(null)}
            onBlur={() => onHover(null)}
          />
        ))}
      </svg>
      <div className="tm-smallmult-foot">
        <span>{daily.length > 0 ? formatDayLabel(daily[0].day) : ""}</span>
        <span className="muted">peak {peak.toLocaleString()}/day</span>
        <span>{daily.length > 0 ? formatDayLabel(daily[daily.length - 1].day) : ""}</span>
      </div>
    </div>
  );
}

export function ActivityChart({ activity, loading }: ActivityChartProps) {
  const [tooltip, setTooltip] = useState<ChartTooltipState | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const trendTitleId = useId();
  const heatmapTitleId = useId();

  const heatmapSummary = useMemo(() => {
    if (!activity || activity.heatmap.length === 0) return null;
    const peak = activity.heatmap.reduce((best, cell) => (cell.count > best.count ? cell : best));
    return { dow: peak.dow, hour: peak.hour, count: peak.count };
  }, [activity]);

  // Which real calendar dates from the range land on each weekday row.
  const dowDates = useMemo(() => {
    const map = new Map<number, string[]>();
    for (const day of activity?.daily ?? []) {
      const dow = pythonDow(day.day);
      if (dow < 0) continue;
      const list = map.get(dow) ?? [];
      list.push(day.day);
      map.set(dow, list);
    }
    return map;
  }, [activity]);

  // Anchor the weekday row order to the range's first calendar day so the rows
  // read chronologically — a last-7-days window that opens on a Sunday renders
  // Sun→Sat with today last, instead of a fixed Mon→Sun axis that strands the
  // range's opening weekday below later dates.
  const orderedDows = useMemo(() => {
    const firstDay = activity?.daily?.[0]?.day;
    const anchor = firstDay ? pythonDow(firstDay) : 0;
    const start = anchor < 0 ? 0 : anchor;
    return Array.from({ length: 7 }, (_, i) => (start + i) % 7);
  }, [activity]);

  if (loading && !activity) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (!activity) return null;

  const daily = activity.daily;
  const trendSummary =
    daily.length > 0
      ? DAILY_SERIES.map(
          (s) => `${daily.reduce((sum, d) => sum + Number(d[s.key]), 0).toLocaleString()} ${s.label.toLowerCase()}`,
        ).join(", ")
      : "";

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
          <>
            <p className="sr-only">
              Daily activity across {daily.length} days: {trendSummary}.
            </p>
            <div className="tm-smallmult-grid">
              {DAILY_SERIES.map((series) => (
                <MiniTrend
                  key={series.key}
                  daily={daily}
                  seriesKey={series.key}
                  label={series.label}
                  color={seriesColor(series.slot)}
                  onHover={setTooltip}
                />
              ))}
            </div>
          </>
        )}
        <ChartTooltip state={tooltip} />

        {daily.length > 0 ? (
          <>
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
          </>
        ) : null}
      </section>

      <section className="panel tm-chart-card" aria-labelledby={heatmapTitleId}>
        <header className="tm-chart-head">
          <h3 id={heatmapTitleId}>Activity heatmap</h3>
          <span className="tm-heatmap-scale" aria-hidden="true">
            less
            <span className="tm-heatmap-scale-ramp">
              {[0.12, 0.36, 0.6, 0.84, 1].map((step) => (
                <span
                  key={step}
                  style={{
                    background: `color-mix(in srgb, var(--tm-series-1) ${Math.round(
                      step * 100,
                    )}%, var(--bg-input))`,
                  }}
                />
              ))}
            </span>
            more
          </span>
        </header>
        {activity.heatmap.length === 0 ? (
          <p className="muted tm-chart-empty">No activity in this range.</p>
        ) : (
          <>
            <p className="tm-chart-summary">
              {heatmapSummary
                ? `Busiest: ${DOW_LABELS[heatmapSummary.dow] ?? heatmapSummary.dow} at ${formatHourLabel(
                    heatmapSummary.hour,
                  )} (${heatmapSummary.count} event${heatmapSummary.count === 1 ? "" : "s"}).`
                : "No activity in this range."}
            </p>
            <div className="tm-heatmap-scroll">
              <table className="tm-heatmap" role="table" aria-label="Activity by day of week and hour">
                <thead>
                  <tr>
                    <th scope="col" className="tm-heatmap-corner" />
                    {Array.from({ length: 24 }, (_, hour) => (
                      <th key={hour} scope="col" className="tm-heatmap-hour">
                        {hour % 3 === 0 ? formatHourLabel(hour) : ""}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {orderedDows.map((dow) => {
                    const label = DOW_LABELS[dow];
                    const dates = dowDates.get(dow) ?? [];
                    return (
                      <tr key={dow}>
                        <th scope="row" className="tm-heatmap-dow">
                          <span className="tm-heatmap-dow-name">{label}</span>
                          <span className="tm-heatmap-dow-date">{formatDowDates(dates)}</span>
                        </th>
                        {Array.from({ length: 24 }, (_, hour) => {
                          const count = heatmapByCell.get(`${dow}:${hour}`) ?? 0;
                          const intensity = heatmapMax > 0 ? count / heatmapMax : 0;
                          const isPeak =
                            heatmapSummary?.dow === dow &&
                            heatmapSummary?.hour === hour &&
                            count > 0;
                          return (
                            <td key={hour} className="tm-heatmap-cell-wrap">
                              <button
                                type="button"
                                className={`tm-heatmap-cell${count === 0 ? " is-empty" : ""}${
                                  isPeak ? " is-peak" : ""
                                }`}
                                style={
                                  count === 0
                                    ? undefined
                                    : {
                                        // Floor at 22% so the faintest real cell is still
                                        // clearly distinct from an empty one in both themes.
                                        background: `color-mix(in srgb, var(--tm-series-1) ${Math.round(
                                          22 + intensity * 78,
                                        )}%, var(--bg-input))`,
                                      }
                                }
                                aria-label={`${label} ${formatDowDates(dates)}, ${formatHourLabel(
                                  hour,
                                )}: ${count} event${count === 1 ? "" : "s"}`}
                                onMouseEnter={(event) =>
                                  setTooltip({
                                    left: event.clientX,
                                    top: event.clientY,
                                    title: `${label} · ${formatHourLabel(hour)}`,
                                    rows: [
                                      { key: "count", label: "events", value: String(count) },
                                      { key: "dates", label: "covers", value: formatDowDates(dates) },
                                    ],
                                  })
                                }
                                onFocus={() =>
                                  setTooltip({
                                    left: 0,
                                    top: 0,
                                    title: `${label} · ${formatHourLabel(hour)}`,
                                    rows: [
                                      { key: "count", label: "events", value: String(count) },
                                      { key: "dates", label: "covers", value: formatDowDates(dates) },
                                    ],
                                  })
                                }
                                onMouseLeave={() => setTooltip(null)}
                                onBlur={() => setTooltip(null)}
                              />
                            </td>
                          );
                        })}
                      </tr>
                    );
                  })}
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
