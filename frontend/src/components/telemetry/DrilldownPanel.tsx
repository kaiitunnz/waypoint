"use client";

import Link from "next/link";
import { useId, useMemo } from "react";

import { Pager } from "@/components/Pager";
import {
  DRILLDOWN_KIND_OPTIONS,
  factKindLabel,
  formatCompactNumber,
  shortId,
  toolOutcomeLabel,
  toolOutcomeTone,
  transitionLabel,
  turnKindLabel,
  ToneWithNeutral,
} from "@/lib/telemetry";
import { DrilldownItem, TelemetryDrilldown, TelemetryFactKind } from "@/lib/types";

interface DrilldownPanelProps {
  drilldown: TelemetryDrilldown | null;
  loading: boolean;
  kind: TelemetryFactKind;
  onKindChange: (kind: TelemetryFactKind) => void;
  page: number;
  onPageChange: (page: number) => void;
  pageSize: number;
}

const OUTCOME_GLYPH: Record<ToneWithNeutral, string> = {
  good: "●",
  danger: "▲",
  warn: "■",
  neutral: "○",
};

// Durations are captured but many calls are 0ms (fast or backfilled), so a
// uniform "· 0ms" is pure noise. Render only meaningful (>0) durations, subtly.
function formatDuration(ms: number): string {
  if (ms >= 60000) {
    const minutes = Math.floor(ms / 60000);
    const seconds = Math.round((ms % 60000) / 1000);
    return seconds > 0 ? `${minutes}m ${seconds}s` : `${minutes}m`;
  }
  if (ms >= 1000) return `${(ms / 1000).toFixed(ms < 10000 ? 1 : 0)}s`;
  return `${ms}ms`;
}

function countBy(items: DrilldownItem[], key: (item: DrilldownItem) => string): Map<string, number> {
  const counts = new Map<string, number>();
  for (const item of items) {
    const bucket = key(item);
    counts.set(bucket, (counts.get(bucket) ?? 0) + 1);
  }
  return counts;
}

// The primary line + status glyph for a row, split so the outcome is stated
// exactly once (as the status indicator) rather than baked into the label too.
function rowStatus(item: DrilldownItem): { tone: ToneWithNeutral; text: string } | null {
  switch (item.kind) {
    case "tool_call":
      return { tone: toolOutcomeTone(item.outcome), text: toolOutcomeLabel(item.outcome) };
    case "session_lifecycle": {
      const tone: ToneWithNeutral = item.transition === "error" ? "danger" : "neutral";
      return { tone, text: transitionLabel(item.transition ?? "") };
    }
    default:
      return null;
  }
}

function primaryLabel(item: DrilldownItem): string {
  if (item.kind === "tool_call") return item.tool_name ?? item.label;
  if (item.kind === "turn") return turnKindLabel(item.turn_kind ?? "");
  return item.label;
}

// Secondary detail that is genuinely per-row (not the outcome, not the session
// id / time which live in the meta column).
function rowDetail(item: DrilldownItem): string | null {
  switch (item.kind) {
    case "tool_call":
      return item.tool_category ?? null;
    case "turn":
      return item.model ?? null;
    case "context_snapshot":
      return item.occupancy_percent !== null && item.occupancy_percent !== undefined
        ? `${Math.round(item.occupancy_percent)}% of ${item.window_tokens ?? "?"}`
        : item.used_tokens !== null && item.used_tokens !== undefined
          ? `${formatCompactNumber(item.used_tokens)} tokens`
          : null;
    case "limit_snapshot":
      return [
        item.used_percent !== null && item.used_percent !== undefined
          ? `${Math.round(item.used_percent)}%`
          : null,
        item.account_key,
      ]
        .filter(Boolean)
        .join(" · ") || null;
    default:
      return null;
  }
}

function SummaryHeader({
  kind,
  items,
  total,
  shown,
}: {
  kind: TelemetryFactKind;
  items: DrilldownItem[];
  total: number;
  shown: number;
}) {
  const statusChips = useMemo(() => {
    if (kind === "tool_call") {
      const byOutcome = countBy(items, (i) => i.outcome ?? "unknown");
      return [...byOutcome.entries()]
        .map(([outcome, count]) => ({
          label: toolOutcomeLabel(outcome),
          tone: toolOutcomeTone(outcome),
          count,
        }))
        .sort((a, b) => b.count - a.count);
    }
    if (kind === "turn") {
      const byTurn = countBy(items, (i) => i.turn_kind ?? "unknown");
      return [...byTurn.entries()]
        .map(([turnKind, count]) => ({
          label: turnKindLabel(turnKind),
          tone: "neutral" as ToneWithNeutral,
          count,
        }))
        .sort((a, b) => b.count - a.count);
    }
    if (kind === "session_lifecycle") {
      const byTransition = countBy(items, (i) => i.transition ?? "unknown");
      return [...byTransition.entries()]
        .map(([transition, count]) => ({
          label: transitionLabel(transition),
          tone: (transition === "error" ? "danger" : "neutral") as ToneWithNeutral,
          count,
        }))
        .sort((a, b) => b.count - a.count);
    }
    return [];
  }, [kind, items]);

  const topTools = useMemo(() => {
    if (kind !== "tool_call") return [];
    const byTool = countBy(items, (i) => i.tool_name ?? i.label);
    const sorted = [...byTool.entries()].sort((a, b) => b[1] - a[1]);
    const head = sorted.slice(0, 5);
    const rest = sorted.slice(5);
    const restTotal = rest.reduce((sum, [, count]) => sum + count, 0);
    return { head, restCount: rest.length, restTotal };
  }, [kind, items]);

  const scoped = shown < total;

  return (
    <div className="tm-drill-summary">
      <div className="tm-drill-summary-hero">
        <span className="tm-drill-summary-count">{formatCompactNumber(total)}</span>
        <span className="tm-drill-summary-caption">{factKindLabel(kind).toLowerCase()} in range</span>
      </div>

      {statusChips.length > 0 ? (
        <div className="tm-drill-chips" role="list" aria-label="Outcome breakdown (shown page)">
          {statusChips.map((chip) => (
            <span key={chip.label} className={`tm-drill-chip tone-${chip.tone}`} role="listitem">
              <span className="tm-drill-chip-glyph" aria-hidden="true">
                {OUTCOME_GLYPH[chip.tone]}
              </span>
              {chip.label}
              <span className="tm-drill-chip-count">{chip.count}</span>
            </span>
          ))}
        </div>
      ) : null}

      {kind === "tool_call" && "head" in topTools && topTools.head.length > 0 ? (
        <div className="tm-drill-tools">
          <span className="tm-drill-tools-label">Top tools</span>
          <div className="tm-drill-chips" role="list" aria-label="Top tools (shown page)">
            {topTools.head.map(([tool, count]) => (
              <span key={tool} className="tm-drill-chip" role="listitem">
                {tool}
                <span className="tm-drill-chip-count">{count}</span>
              </span>
            ))}
            {topTools.restCount > 0 ? (
              <span className="tm-drill-chip is-rest">
                +{topTools.restCount} more
                <span className="tm-drill-chip-count">{topTools.restTotal}</span>
              </span>
            ) : null}
          </div>
        </div>
      ) : null}

      {statusChips.length > 0 && scoped ? (
        <p className="tm-drill-scope muted">
          Breakdown covers this page ({shown} of {formatCompactNumber(total)}).
        </p>
      ) : null}
    </div>
  );
}

export function DrilldownPanel({
  drilldown,
  loading,
  kind,
  onKindChange,
  page,
  onPageChange,
  pageSize,
}: DrilldownPanelProps) {
  const titleId = useId();
  const items = drilldown?.items ?? [];
  const total = drilldown?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const pageStart = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const pageEnd = Math.min(total, page * pageSize);

  return (
    <section className="panel tm-chart-card" aria-labelledby={titleId}>
      <header className="tm-chart-head">
        <h3 id={titleId}>Drilldown</h3>
        <label className="tm-chart-select-label">
          Kind
          <select
            className="tm-chart-select"
            value={kind}
            onChange={(event) => onKindChange(event.target.value as TelemetryFactKind)}
          >
            {DRILLDOWN_KIND_OPTIONS.map((option) => (
              <option key={option.value} value={option.value}>
                {option.label}
              </option>
            ))}
          </select>
        </label>
      </header>

      {loading && !drilldown ? (
        <p className="muted tm-chart-empty" aria-busy="true">
          Loading…
        </p>
      ) : items.length === 0 ? (
        <p className="muted tm-chart-empty">No facts match this filter and range.</p>
      ) : (
        <>
          <SummaryHeader kind={kind} items={items} total={total} shown={items.length} />

          <ul className="tm-drilldown-list">
            {items.map((item) => {
              const status = rowStatus(item);
              const detail = rowDetail(item);
              const duration =
                item.kind === "tool_call" &&
                item.duration_ms !== null &&
                item.duration_ms !== undefined &&
                item.duration_ms > 0
                  ? formatDuration(item.duration_ms)
                  : null;
              return (
                <li key={`${item.kind}:${item.fact_id}`} className="tm-drilldown-row">
                  {status ? (
                    <span
                      className={`tm-drill-status tone-${status.tone}`}
                      title={status.text}
                    >
                      <span className="tm-drill-status-glyph" aria-hidden="true">
                        {OUTCOME_GLYPH[status.tone]}
                      </span>
                      <span className="sr-only">{status.text}</span>
                    </span>
                  ) : (
                    <span className="tm-drill-status is-none" aria-hidden="true" />
                  )}

                  <div className="tm-drilldown-main">
                    <span className="tm-drilldown-label">
                      {primaryLabel(item)}
                      {status ? <span className="tm-drill-status-text"> {status.text}</span> : null}
                      {duration ? <span className="tm-drill-duration"> · {duration}</span> : null}
                    </span>
                    {detail ? <span className="tm-drilldown-secondary muted">{detail}</span> : null}
                  </div>

                  <div className="tm-drilldown-meta">
                    <Link className="tm-drilldown-session" href={`/session/${item.session_id}`}>
                      {shortId(item.session_id)}
                    </Link>
                    <span className="tm-drilldown-time">
                      {new Date(item.occurred_at).toLocaleString()}
                    </span>
                  </div>
                </li>
              );
            })}
          </ul>
          <Pager
            page={page}
            totalPages={totalPages}
            total={total}
            pageStart={pageStart}
            pageEnd={pageEnd}
            onPage={onPageChange}
            label="facts"
          />
        </>
      )}
    </section>
  );
}
