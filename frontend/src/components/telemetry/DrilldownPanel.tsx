"use client";

import Link from "next/link";
import { useId } from "react";

import { Pager } from "@/components/Pager";
import {
  DRILLDOWN_KIND_OPTIONS,
  toolOutcomeLabel,
  toolOutcomeTone,
  transitionLabel,
  turnKindLabel,
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

function secondaryLine(item: DrilldownItem): string {
  switch (item.kind) {
    case "session_lifecycle":
      return transitionLabel(item.transition ?? "");
    case "turn":
      return [turnKindLabel(item.turn_kind ?? ""), item.model].filter(Boolean).join(" · ");
    case "tool_call": {
      const parts = [item.tool_category, toolOutcomeLabel(item.outcome)];
      if (item.duration_ms !== null && item.duration_ms !== undefined) {
        parts.push(`${item.duration_ms}ms`);
      }
      return parts.filter(Boolean).join(" · ");
    }
    case "context_snapshot":
      return item.occupancy_percent !== null && item.occupancy_percent !== undefined
        ? `${Math.round(item.occupancy_percent)}% of ${item.window_tokens ?? "?"}`
        : `${item.used_tokens ?? "?"} tokens`;
    case "limit_snapshot":
      return `${item.used_percent !== null && item.used_percent !== undefined ? Math.round(item.used_percent) : "?"}% · ${item.account_key ?? ""}`;
    default:
      return "";
  }
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
          <ul className="tm-drilldown-list">
            {items.map((item) => {
              const tone = item.kind === "tool_call" ? toolOutcomeTone(item.outcome) : null;
              return (
                <li
                  key={`${item.kind}:${item.fact_id}`}
                  className={`tm-drilldown-row${tone ? ` tone-${tone}` : ""}`}
                >
                  <div className="tm-drilldown-main">
                    <span className="tm-drilldown-label">{item.label}</span>
                    <span className="tm-drilldown-secondary muted">{secondaryLine(item)}</span>
                  </div>
                  <div className="tm-drilldown-meta">
                    <Link className="tm-drilldown-session" href={`/session/${item.session_id}`}>
                      {item.session_id.slice(0, 10)}
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
