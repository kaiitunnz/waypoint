"use client";

import { useId, useMemo, useState } from "react";

import { ChartTooltip, ChartTooltipState } from "@/components/telemetry/ChartTooltip";
import {
  TOKEN_GROUP_BY_OPTIONS,
  coverageLabel,
  formatCompactNumber,
  orderedTokenCategories,
  tokenCategoryLabel,
} from "@/lib/telemetry";
import { TelemetryTokens, TokenGroupBy } from "@/lib/types";

interface TokenChartProps {
  tokens: TelemetryTokens | null;
  loading: boolean;
  groupBy: TokenGroupBy;
  onGroupByChange: (groupBy: TokenGroupBy) => void;
}

const CHART_W = 640;
const CHART_H = 220;
const PAD_LEFT = 40;
const PAD_RIGHT = 12;
const PAD_TOP = 12;
const PAD_BOTTOM = 26;
const SEGMENT_GAP = 2;
const BAR_GAP_RATIO = 0.28;
const MAX_RANKED_GROUPS = 7;

function seriesColor(index: number): string {
  return `var(--tm-series-${(index % 8) + 1})`;
}

function niceMax(value: number): number {
  if (value <= 0) return 1;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const residual = value / magnitude;
  const niceResidual = residual <= 1 ? 1 : residual <= 2 ? 2 : residual <= 5 ? 5 : 10;
  return niceResidual * magnitude;
}

function roundedTopRectPath(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.max(0, Math.min(r, w / 2, h));
  if (radius <= 0) {
    return `M ${x} ${y} h ${w} v ${h} h ${-w} Z`;
  }
  return [
    `M ${x} ${y + radius}`,
    `a ${radius} ${radius} 0 0 1 ${radius} ${-radius}`,
    `h ${w - 2 * radius}`,
    `a ${radius} ${radius} 0 0 1 ${radius} ${radius}`,
    `v ${h - radius}`,
    `h ${-w}`,
    `Z`,
  ].join(" ");
}

// Horizontal bar rounded only at its free (right) end; square at the axis.
function roundedRightRectPath(x: number, y: number, w: number, h: number, r: number): string {
  const radius = Math.max(0, Math.min(r, w, h / 2));
  if (radius <= 0 || w <= 0) {
    return `M ${x} ${y} h ${Math.max(w, 0)} v ${h} h ${-Math.max(w, 0)} Z`;
  }
  return [
    `M ${x} ${y}`,
    `h ${w - radius}`,
    `a ${radius} ${radius} 0 0 1 ${radius} ${radius}`,
    `v ${h - 2 * radius}`,
    `a ${radius} ${radius} 0 0 1 ${-radius} ${radius}`,
    `h ${-(w - radius)}`,
    `Z`,
  ].join(" ");
}

export function TokenChart({ tokens, loading, groupBy, onGroupByChange }: TokenChartProps) {
  const [tooltip, setTooltip] = useState<ChartTooltipState | null>(null);
  const [tableOpen, setTableOpen] = useState(false);
  const titleId = useId();

  const timeSeries = groupBy === "time" ? tokens?.series ?? [] : [];

  const categories = useMemo(() => {
    const points = groupBy === "time" ? tokens?.series ?? [] : [];
    const merged: Record<string, number> = {};
    for (const point of points) {
      for (const [key, value] of Object.entries(point.totals)) {
        merged[key] = (merged[key] ?? 0) + value;
      }
    }
    return orderedTokenCategories(merged);
  }, [groupBy, tokens]);

  const rankedGroups = useMemo(() => {
    const rawGroups = groupBy !== "time" ? tokens?.groups ?? [] : [];
    const withValues = rawGroups.map((group) => ({
      group,
      value: group.display_total ?? Object.values(group.totals).reduce((a, b) => a + b, 0),
    }));
    withValues.sort((a, b) => b.value - a.value);
    if (withValues.length <= MAX_RANKED_GROUPS + 1) return withValues;
    const head = withValues.slice(0, MAX_RANKED_GROUPS);
    const tail = withValues.slice(MAX_RANKED_GROUPS);
    const otherValue = tail.reduce((sum, item) => sum + item.value, 0);
    return [
      ...head,
      {
        group: {
          key: "__other__",
          label: `Other (${tail.length})`,
          totals: {},
          display_total: otherValue,
          coverage: "partial" as const,
        },
        value: otherValue,
      },
    ];
  }, [groupBy, tokens]);

  if (loading && !tokens) {
    return <div className="panel tm-chart-card is-loading" aria-busy="true" />;
  }
  if (!tokens) return null;

  const hasData =
    groupBy === "time" ? timeSeries.some((p) => Object.keys(p.totals).length > 0) : rankedGroups.length > 0;

  const plotW = CHART_W - PAD_LEFT - PAD_RIGHT;
  const plotH = CHART_H - PAD_TOP - PAD_BOTTOM;

  return (
    <section className="panel tm-chart-card" aria-labelledby={titleId}>
      <header className="tm-chart-head">
        <h3 id={titleId}>Token usage</h3>
        <label className="tm-chart-select-label">
          Group by
          <select
            className="tm-chart-select"
            value={groupBy}
            onChange={(event) => onGroupByChange(event.target.value as TokenGroupBy)}
          >
            {TOKEN_GROUP_BY_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </header>

      {!hasData ? (
        <p className="muted tm-chart-empty">No token activity in this range.</p>
      ) : groupBy === "time" ? (
        <TimeSeriesBars
          series={timeSeries}
          categories={categories}
          plotW={plotW}
          plotH={plotH}
          onHover={setTooltip}
        />
      ) : (
        <RankedBars items={rankedGroups} plotW={plotW} plotH={plotH} onHover={setTooltip} />
      )}
      <ChartTooltip state={tooltip} />

      {hasData ? (
        <div className="tm-chart-legend" role="list" aria-label="Series">
          {(groupBy === "time" ? categories : rankedGroups.map((g) => g.group.label)).map(
            (label, index) => (
              <span key={typeof label === "string" ? label : index} className="tm-legend-item" role="listitem">
                <span
                  className="tm-legend-swatch"
                  aria-hidden="true"
                  style={{ background: seriesColor(index) }}
                />
                {groupBy === "time" ? tokenCategoryLabel(label as string) : label}
              </span>
            ),
          )}
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
        groupBy === "time" ? (
          <div className="tm-table-wrap">
            <table className="tm-data-table">
              <caption className="sr-only">Token usage over time by category</caption>
              <thead>
                <tr>
                  <th scope="col">Bucket</th>
                  {categories.map((category) => (
                    <th scope="col" key={category}>
                      {tokenCategoryLabel(category)}
                    </th>
                  ))}
                  <th scope="col">Total</th>
                </tr>
              </thead>
              <tbody>
                {timeSeries.map((point) => (
                  <tr key={point.bucket_start}>
                    <th scope="row">{new Date(point.bucket_start).toLocaleString()}</th>
                    {categories.map((category) => (
                      <td key={category}>{formatCompactNumber(point.totals[category] ?? 0)}</td>
                    ))}
                    <td>
                      {point.display_total !== null ? formatCompactNumber(point.display_total) : "—"}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <div className="tm-table-wrap">
            <table className="tm-data-table">
              <caption className="sr-only">Token usage grouped by {groupBy}</caption>
              <thead>
                <tr>
                  <th scope="col">Group</th>
                  <th scope="col">Tokens</th>
                  <th scope="col">Coverage</th>
                </tr>
              </thead>
              <tbody>
                {rankedGroups.map(({ group, value }) => (
                  <tr key={group.key}>
                    <th scope="row">{group.label}</th>
                    <td>{formatCompactNumber(value)}</td>
                    <td>{coverageLabel(group.coverage)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )
      ) : null}
    </section>
  );
}

function TimeSeriesBars({
  series,
  categories,
  plotW,
  plotH,
  onHover,
}: {
  series: TelemetryTokens["series"];
  categories: string[];
  plotW: number;
  plotH: number;
  onHover: (state: ChartTooltipState | null) => void;
}) {
  const totals = series.map((point) =>
    categories.reduce((sum, category) => sum + (point.totals[category] ?? 0), 0),
  );
  const max = niceMax(Math.max(...totals, 0));
  const barSlot = plotW / Math.max(series.length, 1);
  const barW = barSlot * (1 - BAR_GAP_RATIO);

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${CHART_H}`}
      className="tm-chart-svg"
      role="img"
      aria-label={`Token usage across ${series.length} time buckets, up to ${formatCompactNumber(max)} tokens`}
    >
      <g transform={`translate(${PAD_LEFT},${PAD_TOP})`}>
        {[0, 0.5, 1].map((fraction) => {
          const y = plotH - plotH * fraction;
          return (
            <g key={fraction}>
              <line x1={0} y1={y} x2={plotW} y2={y} className="tm-chart-gridline" />
              <text x={-8} y={y} className="tm-chart-axis-label" textAnchor="end" dy="0.32em">
                {formatCompactNumber(max * fraction)}
              </text>
            </g>
          );
        })}
        {series.map((point, i) => {
          const x = i * barSlot + (barSlot - barW) / 2;
          const usableH = plotH - (categories.length - 1) * SEGMENT_GAP;
          let cursorY = plotH;
          const segments = categories
            .map((category, ci) => {
              const value = point.totals[category] ?? 0;
              const h = max > 0 ? (value / max) * usableH : 0;
              cursorY -= h;
              const segY = cursorY;
              cursorY -= SEGMENT_GAP;
              return { category, ci, value, y: segY, h };
            })
            .filter((segment) => segment.h > 0);
          const topIndex = segments.length - 1;
          const total = totals[i];
          return (
            <g
              key={point.bucket_start}
              tabIndex={0}
              role="img"
              aria-label={`${new Date(point.bucket_start).toLocaleString()}: ${formatCompactNumber(total)} tokens`}
              className="tm-bar-group"
              onMouseEnter={(event) =>
                onHover({
                  left: event.clientX,
                  top: event.clientY,
                  title: new Date(point.bucket_start).toLocaleString(),
                  rows: categories
                    .filter((c) => (point.totals[c] ?? 0) > 0)
                    .map((c, idx) => ({
                      key: c,
                      label: tokenCategoryLabel(c),
                      value: formatCompactNumber(point.totals[c] ?? 0),
                      color: seriesColor(idx),
                    })),
                })
              }
              onMouseMove={(event) =>
                onHover({
                  left: event.clientX,
                  top: event.clientY,
                  title: new Date(point.bucket_start).toLocaleString(),
                  rows: categories
                    .filter((c) => (point.totals[c] ?? 0) > 0)
                    .map((c, idx) => ({
                      key: c,
                      label: tokenCategoryLabel(c),
                      value: formatCompactNumber(point.totals[c] ?? 0),
                      color: seriesColor(idx),
                    })),
                })
              }
              onFocus={() =>
                onHover({
                  left: 0,
                  top: 0,
                  title: new Date(point.bucket_start).toLocaleString(),
                  rows: categories
                    .filter((c) => (point.totals[c] ?? 0) > 0)
                    .map((c, idx) => ({
                      key: c,
                      label: tokenCategoryLabel(c),
                      value: formatCompactNumber(point.totals[c] ?? 0),
                      color: seriesColor(idx),
                    })),
                })
              }
              onMouseLeave={() => onHover(null)}
              onBlur={() => onHover(null)}
            >
              {segments.map((segment, idx) =>
                idx === topIndex ? (
                  <path
                    key={segment.category}
                    d={roundedTopRectPath(x, segment.y, barW, segment.h, 3)}
                    fill={seriesColor(segment.ci)}
                  />
                ) : (
                  <rect
                    key={segment.category}
                    x={x}
                    y={segment.y}
                    width={barW}
                    height={segment.h}
                    fill={seriesColor(segment.ci)}
                  />
                ),
              )}
            </g>
          );
        })}
        <line x1={0} y1={plotH} x2={plotW} y2={plotH} className="tm-chart-axis" />
      </g>
    </svg>
  );
}

function RankedBars({
  items,
  plotW,
  plotH,
  onHover,
}: {
  items: { group: TelemetryTokens["groups"][number]; value: number }[];
  plotW: number;
  plotH: number;
  onHover: (state: ChartTooltipState | null) => void;
}) {
  const max = niceMax(Math.max(...items.map((item) => item.value), 0));
  const rowSlot = plotH / Math.max(items.length, 1);
  const barH = Math.min(24, rowSlot * (1 - BAR_GAP_RATIO));
  const labelW = 96;

  return (
    <svg
      viewBox={`0 0 ${CHART_W} ${Math.max(plotH, items.length * rowSlot) + PAD_TOP + PAD_BOTTOM}`}
      className="tm-chart-svg"
      role="img"
      aria-label={`Token usage ranked across ${items.length} groups, up to ${formatCompactNumber(max)} tokens`}
    >
      <g transform={`translate(${PAD_LEFT + labelW},${PAD_TOP})`}>
        {items.map(({ group, value }, i) => {
          const y = i * rowSlot + (rowSlot - barH) / 2;
          const w = max > 0 ? (value / max) * (plotW - labelW) : 0;
          return (
            <g
              key={group.key}
              tabIndex={0}
              role="img"
              aria-label={`${group.label}: ${formatCompactNumber(value)} tokens`}
              className="tm-bar-group"
              onMouseEnter={(event) =>
                onHover({
                  left: event.clientX,
                  top: event.clientY,
                  title: group.label,
                  rows: [{ key: group.key, label: "tokens", value: formatCompactNumber(value) }],
                })
              }
              onMouseMove={(event) =>
                onHover({
                  left: event.clientX,
                  top: event.clientY,
                  title: group.label,
                  rows: [{ key: group.key, label: "tokens", value: formatCompactNumber(value) }],
                })
              }
              onMouseLeave={() => onHover(null)}
              onFocus={() =>
                onHover({
                  left: 0,
                  top: 0,
                  title: group.label,
                  rows: [{ key: group.key, label: "tokens", value: formatCompactNumber(value) }],
                })
              }
              onBlur={() => onHover(null)}
            >
              <text
                x={-8}
                y={y + barH / 2}
                textAnchor="end"
                dy="0.32em"
                className="tm-chart-axis-label tm-rowlabel"
              >
                {group.label}
              </text>
              <path d={roundedRightRectPath(0, y, w, barH, 4)} fill={seriesColor(i)} />
            </g>
          );
        })}
      </g>
    </svg>
  );
}
