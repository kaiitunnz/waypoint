"use client";

import { coverageLabel, formatCompactNumber, orderedTokenCategories, tokenCategoryLabel } from "@/lib/telemetry";
import { TelemetryOverview } from "@/lib/types";
import { formatRelativeTime, usageTone } from "@/lib/usage";

interface OverviewCardsProps {
  overview: TelemetryOverview | null;
  loading: boolean;
}

function StatRow({ label, value }: { label: string; value: string | number }) {
  return (
    <div className="tm-stat-row">
      <span className="tm-stat-row-label">{label}</span>
      <span className="tm-stat-row-value">{value}</span>
    </div>
  );
}

export function OverviewCards({ overview, loading }: OverviewCardsProps) {
  if (loading && !overview) {
    return (
      <section className="tm-overview-grid" aria-busy="true">
        {Array.from({ length: 4 }, (_, i) => (
          <div key={i} className="panel tm-overview-card is-loading" />
        ))}
      </section>
    );
  }

  if (!overview) {
    return null;
  }

  const { tokens, sessions, turns, tool_calls: toolCalls, alerts } = overview;
  const categories = orderedTokenCategories(tokens.totals);
  const alertCount = alerts.context.length + alerts.limits.length;

  return (
    <section className="tm-overview-grid" aria-label="Overview">
      <article className="panel tm-overview-card" aria-label="Token usage">
        <header className="tm-overview-card-head">
          <h3>Tokens</h3>
          <span className="tm-overview-card-badge">{coverageLabel(tokens.coverage)}</span>
        </header>
        {tokens.safe_total && tokens.display_total !== null ? (
          <p className="tm-overview-hero">{formatCompactNumber(tokens.display_total)}</p>
        ) : (
          <p className="tm-overview-hero-note">No safe grand total — categories below don’t sum</p>
        )}
        <dl className="tm-stat-list">
          {categories.length === 0 ? (
            <p className="muted">No token activity in range.</p>
          ) : (
            categories.map((category) => (
              <StatRow
                key={category}
                label={tokenCategoryLabel(category)}
                value={formatCompactNumber(tokens.totals[category] ?? 0)}
              />
            ))
          )}
        </dl>
        {tokens.meter_coverage_percent !== null ? (
          <p className="tm-overview-footnote">
            Metered on {Math.round(tokens.meter_coverage_percent)}% of tracked turns
          </p>
        ) : null}
      </article>

      <article className="panel tm-overview-card" aria-label="Sessions">
        <header className="tm-overview-card-head">
          <h3>Sessions</h3>
        </header>
        <p className="tm-overview-hero">{sessions.active_now}</p>
        <p className="tm-overview-hero-caption">active now</p>
        <dl className="tm-stat-list">
          <StatRow label="Created" value={sessions.created} />
          <StatRow label="Exited" value={sessions.exited} />
          <StatRow label="Interrupted" value={sessions.interrupted} />
          <StatRow label="Errored" value={sessions.error} />
        </dl>
      </article>

      <article className="panel tm-overview-card" aria-label="Turns and tool calls">
        <header className="tm-overview-card-head">
          <h3>Activity</h3>
        </header>
        <p className="tm-overview-hero">{formatCompactNumber(turns.user + turns.agent)}</p>
        <p className="tm-overview-hero-caption">turns</p>
        <dl className="tm-stat-list">
          <StatRow label="User turns" value={turns.user} />
          <StatRow label="Agent turns" value={turns.agent} />
          <StatRow label="Tool calls" value={toolCalls} />
        </dl>
      </article>

      <article
        className={`panel tm-overview-card tm-alerts-card${alertCount > 0 ? " has-alerts" : ""}`}
        aria-label="Alerts"
      >
        <header className="tm-overview-card-head">
          <h3>Alerts</h3>
          <span className="tm-overview-card-badge">{alertCount}</span>
        </header>
        {overview.limit_card_hidden ? (
          <p className="muted tm-overview-footnote">
            {overview.limit_card_hidden_reason ??
              "Provider limits hidden while a session filter is active."}
          </p>
        ) : null}
        {alertCount === 0 ? (
          <p className="muted">Nothing above threshold.</p>
        ) : (
          <ul className="tm-alert-list">
            {alerts.context.map((snapshot) => {
              const tone = usageTone(snapshot.percent);
              return (
                <li key={snapshot.session_id} className={`tm-alert-row tone-${tone}`}>
                  <span className="tm-alert-glyph" aria-hidden="true">
                    {tone === "danger" ? "▲" : "●"}
                  </span>
                  <span className="tm-alert-text">
                    Context {snapshot.percent !== null ? Math.round(snapshot.percent) : "—"}%
                    <span className="muted"> · {snapshot.session_id.slice(0, 12)}</span>
                  </span>
                  <span className="tm-alert-time">{formatRelativeTime(snapshot.updated_at)}</span>
                </li>
              );
            })}
            {alerts.limits.map((snapshot) => {
              const tone = usageTone(snapshot.used_percent);
              return (
                <li
                  key={`${snapshot.backend}:${snapshot.account_key}:${snapshot.window_id}`}
                  className={`tm-alert-row tone-${tone}`}
                >
                  <span className="tm-alert-glyph" aria-hidden="true">
                    {tone === "danger" ? "▲" : "●"}
                  </span>
                  <span className="tm-alert-text">
                    {snapshot.label ?? snapshot.window_id} {Math.round(snapshot.used_percent)}%
                    <span className="muted"> · {snapshot.backend}</span>
                  </span>
                  <span className="tm-alert-time">{formatRelativeTime(snapshot.updated_at)}</span>
                </li>
              );
            })}
          </ul>
        )}
      </article>
    </section>
  );
}
