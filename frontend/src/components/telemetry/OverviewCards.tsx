"use client";

import {
  coverageLabel,
  formatCompactNumber,
  splitTokenTiers,
  tokenCategoryColor,
  tokenCategoryLabel,
  TOKEN_TIER_NEW_WORK,
  shortId,
} from "@/lib/telemetry";
import { TelemetryOverview } from "@/lib/types";
import { formatRelativeTime, usageTone } from "@/lib/usage";

interface OverviewCardsProps {
  overview: TelemetryOverview | null;
  loading: boolean;
}

function StatRow({
  label,
  value,
  tone,
  swatch,
}: {
  label: string;
  value: string | number;
  tone?: "danger" | "warn";
  swatch?: string;
}) {
  return (
    <div className={`tm-stat-row${tone ? ` tone-${tone}` : ""}`}>
      <span className="tm-stat-row-label">
        {swatch ? (
          <span className="tm-stat-swatch" aria-hidden="true" style={{ background: swatch }} />
        ) : null}
        {label}
      </span>
      <span className="tm-stat-row-value">{value}</span>
    </div>
  );
}

function TokensCard({ tokens }: { tokens: TelemetryOverview["tokens"] }) {
  const tiers = splitTokenTiers(tokens.totals);
  // Headline is the new-work grand total only. Derive it from the raw buckets
  // rather than `display_total`: the four new-work buckets never overlap, so the
  // sum is always safe, and it stays correct regardless of whether the backend
  // has already switched `display_total` to exclude cache reads.
  const newWorkTotal = tiers.newWork;
  // Cached re-reads are reported as their own standalone value, never summed
  // into the total; prefer the explicit field, falling back to the raw bucket.
  const cachedReread = tokens.cached_read_tokens ?? tiers.reread;
  const hasNewWork = tiers.newWork > 0;
  const hasData = hasNewWork || cachedReread > 0;

  return (
    <article className="panel tm-overview-card" aria-label="Token usage">
      <header className="tm-overview-card-head">
        <h3>Tokens</h3>
        <span className="tm-overview-card-badge">{coverageLabel(tokens.coverage)}</span>
      </header>

      {hasData ? (
        <>
          <div className="tm-overview-hero-block">
            <p className="tm-overview-hero">{formatCompactNumber(newWorkTotal)}</p>
            <p className="tm-overview-hero-caption">new-work tokens</p>
          </div>
          <div className="tm-overview-body">
            {hasNewWork ? (
              <div className="tm-tokens-breakdown">
                {TOKEN_TIER_NEW_WORK.filter((c) => (tokens.totals[c] ?? 0) > 0).map((category) => (
                  <StatRow
                    key={category}
                    label={tokenCategoryLabel(category)}
                    value={formatCompactNumber(tokens.totals[category] ?? 0)}
                    swatch={tokenCategoryColor(category)}
                  />
                ))}
              </div>
            ) : null}
            {cachedReread > 0 ? (
              <div className="tm-token-reread-block">
                <div className="tm-token-reread-head">
                  <span className="tm-token-reread-label">Cached re-reads</span>
                  <span className="tm-token-reread-value">{formatCompactNumber(cachedReread)}</span>
                </div>
              </div>
            ) : null}
          </div>
        </>
      ) : (
        <p className="muted tm-overview-empty">No token activity in range.</p>
      )}

      {tokens.meter_coverage_percent !== null ? (
        <p className="tm-overview-footnote">
          Metered on {Math.round(tokens.meter_coverage_percent)}% of tracked turns
        </p>
      ) : null}
    </article>
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
  const alertCount = alerts.context.length + alerts.limits.length;

  return (
    <section className="tm-overview-grid" aria-label="Overview">
      <TokensCard tokens={tokens} />

      <article className="panel tm-overview-card" aria-label="Sessions">
        <header className="tm-overview-card-head">
          <h3>Sessions</h3>
        </header>
        <div className="tm-overview-hero-block">
          <p className="tm-overview-hero">{formatCompactNumber(sessions.active_now)}</p>
          <p className="tm-overview-hero-caption">active now</p>
        </div>
        <div className="tm-overview-body">
          <dl className="tm-stat-list">
            <StatRow label="Created" value={formatCompactNumber(sessions.created)} />
            <StatRow label="Exited" value={formatCompactNumber(sessions.exited)} />
            <StatRow
              label="Interrupted"
              value={formatCompactNumber(sessions.interrupted)}
              tone={sessions.interrupted > 0 ? "warn" : undefined}
            />
            <StatRow
              label="Errored"
              value={formatCompactNumber(sessions.error)}
              tone={sessions.error > 0 ? "danger" : undefined}
            />
          </dl>
        </div>
      </article>

      <article className="panel tm-overview-card" aria-label="Turns and tool calls">
        <header className="tm-overview-card-head">
          <h3>Activity</h3>
        </header>
        <div className="tm-overview-hero-block">
          <p className="tm-overview-hero">{formatCompactNumber(turns.user + turns.agent)}</p>
          <p className="tm-overview-hero-caption">turns</p>
        </div>
        <div className="tm-overview-body">
          <dl className="tm-stat-list">
            <StatRow label="User turns" value={formatCompactNumber(turns.user)} />
            <StatRow label="Agent turns" value={formatCompactNumber(turns.agent)} />
            <StatRow label="Tool calls" value={formatCompactNumber(toolCalls)} />
          </dl>
        </div>
      </article>

      <article
        className={`panel tm-overview-card tm-alerts-card${alertCount > 0 ? " has-alerts" : ""}`}
        aria-label="Alerts"
      >
        <header className="tm-overview-card-head">
          <h3>Alerts</h3>
          <span className="tm-overview-card-badge">{alertCount}</span>
        </header>
        <div className="tm-overview-hero-block">
          <p className={`tm-overview-hero${alertCount === 0 ? " is-clear" : ""}`}>
            {alertCount === 0 ? "0" : formatCompactNumber(alertCount)}
          </p>
          <p className="tm-overview-hero-caption">
            {alertCount === 0 ? "all clear" : "need attention"}
          </p>
        </div>
        <div className="tm-overview-body">
          {overview.limit_card_hidden ? (
            <p className="muted tm-overview-footnote">
              {overview.limit_card_hidden_reason ??
                "Hidden while a session filter is active."}
            </p>
          ) : null}
          {alertCount === 0 ? (
            <p className="muted tm-overview-empty">Nothing above threshold.</p>
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
                      <span className="muted"> · {shortId(snapshot.session_id)}</span>
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
                      <span className="muted">
                        {" · "}
                        {snapshot.account_label ?? snapshot.account_key} · {snapshot.backend}
                      </span>
                    </span>
                    <span className="tm-alert-time">{formatRelativeTime(snapshot.updated_at)}</span>
                  </li>
                );
              })}
            </ul>
          )}
        </div>
      </article>
    </section>
  );
}
