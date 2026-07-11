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

function percentOf(part: number, whole: number): number {
  return whole > 0 ? Math.round((part / whole) * 100) : 0;
}

function TokensCard({ tokens }: { tokens: TelemetryOverview["tokens"] }) {
  const tiers = splitTokenTiers(tokens.totals);
  const hasData = tiers.total > 0;

  return (
    <article className="panel tm-overview-card" aria-label="Token usage">
      <header className="tm-overview-card-head">
        <h3>Tokens</h3>
        <span className="tm-overview-card-badge">{coverageLabel(tokens.coverage)}</span>
      </header>

      {tokens.safe_total && tokens.display_total !== null ? (
        <div className="tm-overview-hero-block">
          <p className="tm-overview-hero">{formatCompactNumber(tokens.display_total)}</p>
          <p className="tm-overview-hero-caption">total tokens</p>
        </div>
      ) : (
        <p className="tm-overview-hero-note">No safe grand total — the tiers below don’t sum.</p>
      )}

      {hasData ? (
        <div className="tm-overview-body">
          <div
            className="tm-tier-bar"
            role="img"
            aria-label={`${percentOf(tiers.newWork, tiers.total)}% new work, ${percentOf(
              tiers.reread,
              tiers.total,
            )}% re-read context`}
          >
            <span
              className="tm-tier-bar-seg is-newwork"
              style={{ flexGrow: Math.max(tiers.newWork, 0) }}
            />
            <span
              className="tm-tier-bar-seg is-reread"
              style={{ flexGrow: Math.max(tiers.reread, 0) }}
            />
          </div>
          <dl className="tm-tier-list">
            <div className="tm-tier">
              <div className="tm-tier-head">
                <span className="tm-tier-name">
                  <span className="tm-stat-swatch is-newwork" aria-hidden="true" />
                  New work
                </span>
                <span className="tm-tier-value">{formatCompactNumber(tiers.newWork)}</span>
              </div>
              <div className="tm-tier-sub">
                {TOKEN_TIER_NEW_WORK.filter((c) => (tokens.totals[c] ?? 0) > 0).map((category) => (
                  <StatRow
                    key={category}
                    label={tokenCategoryLabel(category)}
                    value={formatCompactNumber(tokens.totals[category] ?? 0)}
                    swatch={tokenCategoryColor(category)}
                  />
                ))}
              </div>
            </div>
            <div className="tm-tier">
              <div className="tm-tier-head">
                <span className="tm-tier-name">
                  <span className="tm-stat-swatch is-reread" aria-hidden="true" />
                  Re-read context
                </span>
                <span className="tm-tier-value">{formatCompactNumber(tiers.reread)}</span>
              </div>
              <p className="tm-tier-note">cheap re-sent prior context</p>
            </div>
          </dl>
        </div>
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
                "Provider limits hidden while a session filter is active."}
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
